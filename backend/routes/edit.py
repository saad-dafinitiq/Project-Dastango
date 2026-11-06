"""Phase 5 endpoints — natural-language edit + intent classification.

An edit writes a new version of the film, so it is a job like a render: it
waits its turn behind the project's other jobs, runs on a worker, and its
progress streams over /ws/progress/{project_id}.
"""
from __future__ import annotations
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

import jobs
from agents.edit_agent import EditAgent, ops
from auth import accounts
from auth.accounts import User
from auth.deps import require_project, require_user
from shared import music
from shared.schemas.edit import EditCommand
from shared.utils.logging import get_logger
from state_manager.state_manager import StateManager

from ..services.pipeline_service import failure_reason, run_and_wait
from ..services import pipeline_service
from .pipeline import RunResponse, _accepted, get_storyboard

router = APIRouter()
log = get_logger("api.edit")
agent = EditAgent()
sm = StateManager()


class EditRequest(BaseModel):
    project_id: str
    query: str = Field(..., min_length=1)


class QueueEditRequest(BaseModel):
    query: str = Field(..., min_length=1)


class ClassifyRequest(BaseModel):
    query: str
    project_id: Optional[str] = None


def _require_owned(project_id: str, user: User) -> None:
    """The project id is in the body here, so the check is explicit."""
    if not accounts.may_access(user, project_id):
        raise HTTPException(404, f"project {project_id} not found")


def _require_film(project_id: str) -> None:
    state = sm.latest(project_id)
    if not state:
        raise HTTPException(404, f"project {project_id} not found")
    if not state.video:
        raise HTTPException(409, "render the film before editing it")


@router.post("/classify")
def classify(req: ClassifyRequest, user: User = Depends(require_user)):
    """Classifying reads the project's script to match character names, so it
    is only allowed against a project this account owns."""
    state = None
    if req.project_id:
        _require_owned(req.project_id, user)
        state = sm.latest(req.project_id)
    intent = agent.classify(EditCommand(project_id=req.project_id or "", query=req.query),
                            state=state)
    return intent.model_dump(mode="json")


@router.post("/apply")
def apply_edit(req: EditRequest, user: User = Depends(require_user)):
    """Apply an edit and answer with the result (the classic page waits for it)."""
    _require_owned(req.project_id, user)
    _require_film(req.project_id)
    # The edit is attributed to the signed-in account, not to whatever the
    # request body claimed.
    try:
        job, final = run_and_wait("edit", req.project_id,
                                  {"query": req.query, "user_id": user.id})
    except TimeoutError as e:
        raise HTTPException(504, f"the edit is still running ({e})")
    if job.status != "succeeded":
        raise HTTPException(400, failure_reason(job.error) or "edit failed")
    return final.get("result") or {}


@router.post("/{project_id}", response_model=RunResponse,
             dependencies=[Depends(require_project)])
def queue_edit(project_id: str, req: QueueEditRequest, user: User = Depends(require_user)):
    """Queue an edit and return at once; progress streams like a render's."""
    _require_film(project_id)
    return _accepted("edit", project_id, "queued", user, max_attempts=1,
                     query=req.query, user_id=user.id)


class OpRequest(BaseModel):
    op: str
    params: dict = Field(default_factory=dict)


@router.get("/ops")
def edit_ops(user: User = Depends(require_user)):
    """Every editing operation, its parameters and its choices (the transition styles and
    speeds): what the storyboard draws its pickers from and what the edit agent may do."""
    return ops.catalogue()


@router.post("/{project_id}/op", dependencies=[Depends(require_project)])
def apply_op(project_id: str, req: OpRequest, user: User = Depends(require_user)):
    """Apply one editing operation (DST-39): what a click in the storyboard does, and the very
    operation a typed sentence is turned into.

    Before a render it changes the storyboard at once and answers with it. On a finished film
    the film has to be cut again, so it queues a job and answers `{job_id, ...}`; progress
    streams like any edit. `400` for an unknown operation or bad parameters, `404` when it does
    not fit this film (no such scene or shot), `409` while the film is being worked on."""
    state = sm.latest(project_id)
    if not state or not state.script:
        raise HTTPException(404, f"project {project_id} has no storyboard")
    try:
        ops.get(req.op)
        ops.parse(req.op, req.params)
    except ops.OpError as e:
        raise HTTPException(400, str(e))
    try:
        ops.check(state, req.op, req.params)
    except ops.OpError as e:
        raise HTTPException(404, str(e))
    busy = [j for j in jobs.list_jobs(project_id=project_id) if not j.done]
    if busy:
        raise HTTPException(409, f"This film is being worked on right now ({busy[0].kind}); "
                                 "try again when it has finished.")
    if state.video and state.video.final_video_path:
        return _accepted("op", project_id, "queued", user, max_attempts=1,
                         op=req.op, params=req.params).model_dump()
    pipeline_service.orchestrator().apply_op(project_id, req.op, req.params)
    return get_storyboard(project_id)


class OpsRequest(BaseModel):
    ops: list[OpRequest] = Field(..., min_length=1, max_length=60)


def _check_items(state, items) -> list:
    """Every op parsed, then all tried in order on a copy; the words each would do."""
    try:
        for it in items:
            ops.get(it["op"])
            ops.parse(it["op"], it["params"])
    except ops.OpError as e:
        raise HTTPException(400, str(e))
    try:
        return ops.check_many(state, items)
    except ops.OpError as e:
        raise HTTPException(404, str(e))


@router.post("/{project_id}/ops/check", dependencies=[Depends(require_project)])
def check_ops(project_id: str, req: OpsRequest):
    """Would this stack of changes apply, in order, to the film as it is? Answers what each would do
    ({labels}); changes nothing. The interface asks this as each change is stacked, so a change that
    cannot be made is refused when it is chosen, not when everything is applied."""
    state = sm.latest(project_id)
    if not state or not state.script:
        raise HTTPException(404, f"project {project_id} has no storyboard")
    return {"labels": _check_items(state, [r.model_dump() for r in req.ops])}


@router.post("/{project_id}/ops", dependencies=[Depends(require_project)])
def apply_ops(project_id: str, req: OpsRequest, user: User = Depends(require_user)):
    """Apply a stack of changes at once (DST-40): one job, one version, the film cut once. Before a
    render they change the storyboard at once and the answer is the storyboard. Statuses as `/op`."""
    state = sm.latest(project_id)
    if not state or not state.script:
        raise HTTPException(404, f"project {project_id} has no storyboard")
    items = [r.model_dump() for r in req.ops]
    _check_items(state, items)
    busy = [j for j in jobs.list_jobs(project_id=project_id) if not j.done]
    if busy:
        raise HTTPException(409, f"This film is being worked on right now ({busy[0].kind}); "
                                 "try again when it has finished.")
    if state.video and state.video.final_video_path:
        return _accepted("ops", project_id, "queued", user, max_attempts=1, items=items).model_dump()
    pipeline_service.orchestrator().apply_ops(project_id, items)
    return get_storyboard(project_id)


@router.get("/log/{project_id}", dependencies=[Depends(require_project)])
def edit_log(project_id: str):
    return sm.edit_history(project_id)


@router.get("/music")
def music_catalogue():
    """The music library (id, mood, length, licence) and the ambience kinds."""
    return {"tracks": music.catalogue(), "moods": music.moods(),
            "ambience": [{"id": k, "label": v["label"], "about": v["about"]} for k, v in music.AMBIENCE.items()],
            "limits": {"max_mb": music.MAX_UPLOAD_BYTES // (1024 * 1024),
                       "min_seconds": music.MIN_UPLOAD_S, "max_seconds": music.MAX_UPLOAD_S,
                       "extensions": list(music.UPLOAD_EXTENSIONS)}}


@router.get("/music/{track_id}/file")
def music_file(track_id: str):
    """A library track to listen to before choosing it."""
    from fastapi.responses import FileResponse
    track = music.library().get(track_id)
    if not track:
        raise HTTPException(404, "no such track")
    return FileResponse(track.path, media_type="audio/mpeg")


@router.post("/{project_id}/music", dependencies=[Depends(require_project)])
async def upload_music(project_id: str, file: UploadFile = File(...), rights: bool = Form(False)):
    """Add a music file to this film. Checked and kept, not yet used: the answer's `track`
    goes to `POST /api/edit/{id}/op` as `set_music` (with a scene, or without for the film)."""
    if not rights:
        raise HTTPException(400, "confirm you have the right to use this music")
    dest = music.uploads_dir(project_id)
    dest.mkdir(parents=True, exist_ok=True)
    incoming = dest / f"_incoming_{uuid.uuid4().hex}"
    size = 0
    try:
        with incoming.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > music.MAX_UPLOAD_BYTES:       # stop reading: do not take the whole file in
                    raise HTTPException(413, f"that file is over {music.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                out.write(chunk)
        try:
            record = music.store_upload(incoming, file.filename or "", dest)
        except music.MusicError as e:
            raise HTTPException(400, str(e)) from None
    finally:
        incoming.unlink(missing_ok=True)
    return {"track": f"upload:{record['id']}", "title": record["title"], "seconds": record["duration_s"]}
