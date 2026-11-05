"""Phase 5 — top-level edit agent with versioned undo.

Flow:
  user query -> IntentClassifier -> planner.plan -> EditExecutor.execute
                                  -> StateManager.snapshot (new version)
The agent itself is stateful via StateManager; LangGraph's MemorySaver concept
is mirrored by storing the running PipelineState per project.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple

from shared.schemas.edit import EditCommand, EditIntent, EditResult
from shared.schemas.pipeline import PipelineState
from shared.utils.logging import get_logger
from state_manager.snapshot import referenced_files, restore_assets
from state_manager.state_manager import StateManager

from .executor import EditExecutor
from .intent_classifier import IntentClassifier
from .planner import EditStep, plan as plan_steps

log = get_logger("edit_agent")


class EditAgent:
    def __init__(self, state_manager: Optional[StateManager] = None):
        self.classifier = IntentClassifier()
        self.executor = EditExecutor()
        self.sm = state_manager or StateManager()

    # ---- public API ------------------------------------------------------

    def classify(self, command: EditCommand,
                 state: Optional[PipelineState] = None) -> EditIntent:
        """Stateless intent classification — used by /api/edit/classify."""
        return self.classifier.classify(command.query, *self._context(state))

    def edit(self, command: EditCommand,
             on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> EditResult:
        """Classify, plan, execute, snapshot. Returns the new version + result.

        `on_progress(kind, info)` hears "understood" once the request is
        classified, then "step" before each step. It may raise to stop the
        edit (that is how a cancelled job stops) — the files go back to the
        saved version either way.
        """
        progress = on_progress or (lambda _kind, _info: None)
        state = self.sm.latest(command.project_id)
        if not state:
            return EditResult(
                success=False,
                intent=EditIntent(intent="noop", target="video", scope="global"),
                error=f"no project state for {command.project_id}",
            )

        intent = self.classifier.classify(command.query, *self._context(state))
        log.info("[%s] classified '%s' -> %s/%s/%s %s",
                 command.project_id, command.query, intent.intent,
                 intent.target, intent.scope, intent.parameters)
        try:
            steps = plan_steps(intent)
        except ValueError as e:
            # Not understood, or missing what it needs: say so, change nothing.
            res = EditResult(success=False, intent=intent, error=str(e))
            self.sm.log_edit(command.project_id, command.query,
                             intent.model_dump(mode="json"), res.model_dump(mode="json"))
            return res
        progress("understood", {"intent": intent.model_dump(mode="json"),
                                "steps": [s.name for s in steps]})

        # Edits change files in place, so start from exactly what the saved
        # version holds: an earlier edit that died half way (a killed worker)
        # must not leave its half-applied change for this one to bake in.
        self._put_back(state)
        affected: List[str] = []
        try:
            for i, step in enumerate(steps):
                progress("step", {"name": step.name, "index": i, "total": len(steps)})
                affected.extend(self.executor.execute(state, step))
        except BaseException as e:  # noqa: BLE001 — a cancelled job stops here too
            self._put_back(state)
            if not isinstance(e, Exception):
                raise
            log.exception("edit execution failed")
            res = EditResult(
                success=False,
                intent=intent,
                affected_assets=affected,
                error=f"{type(e).__name__}: {e}",
            )
            self.sm.log_edit(command.project_id, command.query,
                             intent.model_dump(mode="json"),
                             res.model_dump(mode="json"))
            return res

        # Snapshot the new state.
        version = self.sm.snapshot(
            state,
            asset_paths=self._collect_assets(state),
            description=f"edit: {intent.intent} ({intent.target})",
            # The creator's own words travel with the version, so the history
            # can show what they asked for rather than an intent name.
            edit_intent={**intent.model_dump(mode="json"), "query": command.query},
        )

        result = EditResult(
            success=True,
            intent=intent,
            new_version=version.version,
            affected_assets=list(set(affected)),
            message=f"applied '{intent.intent}' on {intent.target} ({intent.scope})",
        )
        self.sm.log_edit(command.project_id, command.query,
                         intent.model_dump(mode="json"),
                         result.model_dump(mode="json"))
        return result

    def set_shot(self, project_id: str, scene_id: str, shot_id: str,
                 move: Optional[str] = None, framing: Optional[str] = None,
                 reset: bool = False,
                 on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> EditResult:
        """Change one shot of a finished film and cut it again (DST-38).

        Not a sentence the classifier has to read: the creator picked a camera move
        or a framing, so there is nothing to interpret. The choice is stored on the
        scene (`Scene.shot_overrides`) and the film is composed again, which
        re-renders only the scene whose plan changed. Same guarantees as `edit`:
        starts from the saved version's files, and on failure or cancel puts them
        back, so a half-made change never reaches the next one.
        """
        from shared.schemas.story import ShotOverride

        progress = on_progress or (lambda _kind, _info: None)
        state = self.sm.latest(project_id)
        intent = EditIntent(intent="shot_camera", target="video", scope=f"scene:{scene_id}",
                            parameters={"shot_id": shot_id, "move": move,
                                        "framing": framing, "reset": reset})
        if not state or not state.script:
            return EditResult(success=False, intent=intent,
                              error=f"no project state for {project_id}")
        scene = next((s for s in state.script.scenes if s.scene_id == scene_id), None)
        if scene is None:
            return EditResult(success=False, intent=intent, error=f"unknown scene '{scene_id}'")
        progress("understood", {"intent": intent.model_dump(mode="json"),
                                "steps": ["recompose_video"]})
        self._put_back(state)
        try:
            if reset:
                scene.shot_overrides.pop(shot_id, None)
            else:
                current = scene.shot_overrides.get(shot_id) or ShotOverride()
                scene.shot_overrides[shot_id] = ShotOverride(
                    move=move if move is not None else current.move,
                    framing=framing if framing is not None else current.framing)
            progress("step", {"name": "recompose_video", "index": 0, "total": 1})
            affected = self.executor.execute(state, EditStep("recompose_video", "video", "global"))
        except BaseException as e:  # noqa: BLE001 — a cancelled job stops here too
            self._put_back(state)
            if not isinstance(e, Exception):
                raise
            log.exception("shot change failed")
            return EditResult(success=False, intent=intent, error=f"{type(e).__name__}: {e}")
        number = next((i + 1 for i, s in enumerate(state.script.scenes)
                       if s.scene_id == scene_id), 1)
        if reset:
            what = "camera as planned"
        else:
            what = ", ".join(x for x in (move.replace("_", " ") if move else "",
                                         f"{framing} framing" if framing else "") if x)
        version = self.sm.snapshot(
            state, asset_paths=self._collect_assets(state),
            description="edit: shot_camera (video)",
            edit_intent={**intent.model_dump(mode="json"), "query": f"Scene {number}: {what}"})
        return EditResult(success=True, intent=intent, new_version=version.version,
                          affected_assets=list(set(affected)),
                          message=f"changed shot {shot_id}")

    def revert(self, project_id: str, version: int) -> PipelineState:
        return self.sm.revert(project_id, version)

    def history(self, project_id: str) -> List[dict]:
        return self.sm.history(project_id)

    def edit_log(self, project_id: str) -> List[dict]:
        return self.sm.edit_history(project_id)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _put_back(state: PipelineState) -> None:
        """Make the project's files match the version `state` was loaded from."""
        try:
            restore_assets(state.project_id, state.version)
        except Exception:  # noqa: BLE001 — never hide the edit's own error behind this
            log.exception("could not restore %s v%d", state.project_id, state.version)

    @staticmethod
    def _context(state: Optional[PipelineState]
                 ) -> Tuple[List[str], List[str], Dict[str, str], Dict[str, str]]:
        """(scene ids, character ids, character id -> name, scene id -> title) for
        the classifier — titles let "the market scene" find scene_1."""
        if not state or not state.script:
            return [], [], {}, {}
        chars = state.script.characters.characters
        return ([s.scene_id for s in state.script.scenes],
                [c.id for c in chars], {c.id: c.name for c in chars},
                {s.scene_id: s.title for s in state.script.scenes})

    @staticmethod
    def _collect_assets(state: PipelineState) -> List[str]:
        return referenced_files(state)
