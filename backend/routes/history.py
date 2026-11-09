"""Version history + revert endpoints."""
from __future__ import annotations
from typing import Any, Dict, Mapping

from fastapi import APIRouter, Depends, HTTPException

from agents.edit_agent.describe import describe
from agents.orchestrator import RENDER_DESCRIPTIONS as RENDERS
from auth.deps import require_project
from state_manager.history import format_history
from state_manager.state_manager import StateManager

from ..services.pipeline_service import failure_reason, run_and_wait

router = APIRouter()
sm = StateManager()


@router.get("/{project_id}", dependencies=[Depends(require_project)])
def list_history(project_id: str):
    rows = sm.history(project_id)
    if not rows:
        raise HTTPException(404, f"no history for {project_id}")
    return format_history(rows)


@router.get("/{project_id}/film", dependencies=[Depends(require_project)])
def film_versions(project_id: str):
    """The film's versions since it was first rendered, oldest first.

    Storyboard drafts come before that and are left out: going back to one
    would take the creator from a film to a sketch.
    """
    rows = sm.history(project_id)
    first = next((i for i, r in enumerate(rows) if r.get("description") in RENDERS), None)
    if first is None:
        return []
    state = sm.latest(project_id)
    names = ({c.id: c.name for c in state.script.characters.characters}
             if state and state.script else {})
    latest = rows[-1]["version"]
    out = []
    for i, row in enumerate(rows[first:]):
        out.append({"version": row["version"], "created_at": row["created_at"],
                    # What the creator calls it: the first render is cut 1. The storyboard
                    # before it took version 1, which is why `version` starts at 2 (F15).
                    "number": i + 1,
                    "current": row["version"] == latest,
                    **_in_words(row, names, first_render=i == 0)})
    return out


def _in_words(row: Mapping[str, Any], names: Mapping[str, str],
              first_render: bool) -> Dict[str, Any]:
    description = row.get("description") or ""
    intent = row.get("edit_intent") or {}
    if description in RENDERS:
        return {"kind": "render", "label": "First cut" if first_render else "Rendered again",
                "detail": ""}
    if intent.get("intent") == "revert":
        back_to = str(intent.get("scope", "")).lstrip("v")
        return {"kind": "revert", "label": f"Back to version {back_to}", "detail": "",
                "restored": int(back_to) if back_to.isdigit() else None}
    if intent:
        return {"kind": "edit", "label": intent.get("query") or describe(intent, names),
                "detail": describe(intent, names)}
    if description.startswith("re-run phase: "):
        return {"kind": "rerun", "detail": "",
                "label": f"Made the {description.split(': ', 1)[1]} again"}
    return {"kind": "other", "label": description or "Saved", "detail": ""}


@router.post("/{project_id}/revert/{version}", dependencies=[Depends(require_project)])
def revert(project_id: str, version: int):
    """Go back to an earlier version, as a new version (history stays linear).

    Runs on a worker like any job that writes a version, so it waits for the
    project's running job instead of restoring files underneath it.
    """
    target = sm.load_version(project_id, version)
    if target is None:
        raise HTTPException(404, f"no version v{version} for {project_id}")
    # A draft made before the first render has no film. Going back to it from a film would leave the
    # creator with a sketch and no video; the film's own versions (`/film`) are the ones to go back to.
    now = sm.latest(project_id)
    has_film = lambda s: bool(s and s.video and s.video.final_video_path)  # noqa: E731
    if has_film(now) and not has_film(target):
        raise HTTPException(409, f"version v{version} is a storyboard draft with no film, so going back to it "
                                 "would leave this project without a video. Choose a version from the film's "
                                 "own list (GET /api/history/{id}/film).")
    try:
        job, _final = run_and_wait("revert", project_id, {"version": version})
    except TimeoutError as e:
        raise HTTPException(504, f"the revert is still waiting ({e})")
    if job.status != "succeeded":
        raise HTTPException(400, failure_reason(job.error) or "revert failed")
    state = sm.latest(project_id)
    return {"reverted_to": version, "version": state.version,
            "new_state": state.model_dump(mode="json")}
