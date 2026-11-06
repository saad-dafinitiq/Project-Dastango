"""Edit planner — turn an EditIntent into a sequence of executor steps.

We keep this very small: an intent maps to one (sometimes two) executor calls.
`plan` is pure — the edit agent owns the state and its versions, so editing
can go on turn after turn without the planner remembering anything.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List

from shared.schemas.edit import EditIntent

from . import ops
from .vocabulary import ASK_FOR, EDITS, UNCLEAR_MESSAGE, missing


@dataclass
class EditStep:
    name: str            # e.g. "rerun_audio", "apply_filter", "regenerate_scene"
    target: str          # phase target (audio/video_frame/video/script)
    scope: str = "global"
    params: Dict[str, Any] = field(default_factory=dict)


def plan(intent: EditIntent) -> List[EditStep]:
    """Steps for an edit the editor knows how to make — and only those.

    A request it can't name, or one missing what it needs ("change the tone"
    — to what?), raises with what to say instead. It used to fall through to
    re-recording or recutting the film unchanged and report success.
    """
    name = intent.intent
    if name not in EDITS:
        raise ValueError(UNCLEAR_MESSAGE)
    params = dict(intent.parameters or {})
    lacking = missing(name, params)
    if lacking:
        raise ValueError(ASK_FOR.get(lacking[0], f"the edit needs a {lacking[0]}"))
    target = EDITS[name].target
    scope = intent.scope or "global"

    # The two older music intents are the shared set_music operation: one code path.
    if name == "add_background_music":
        name, params, target = "set_music", {"mood": (params or {}).get("mood", "ambient")}, "audio"
    elif name == "remove_background_music":
        name, params, target = "set_music", {"off": True}, "audio"

    if name in ops.OPS:
        # A shared operation (DST-39): its parameters are the intent's, plus where it applies.
        if name == "set_transition" and not scope.startswith("scene:"):
            raise ValueError("which cut? e.g. “a slow fade between scenes 1 and 2”")
        step_params = {**params, "scene": scope.split(":", 1)[1]} if scope.startswith("scene:") else params
        if name == "set_volume" and scope.startswith("character:"):
            raise ValueError("levels are for the music, the voices together or the ambience, "
                             "not one character: use a tone or volume edit on the character")
        return [EditStep(name, target, scope, step_params),
                *[EditStep(a, "video", sc, p) for a, sc, p in ops.after_steps(None, name, step_params)]]

    if target == "script":
        return [
            EditStep("regenerate_script", "script", scope, params),
            EditStep("rerun_audio", "audio", "global"),
            EditStep("rerun_video", "video", "global"),
        ]

    if target == "audio":
        if name in ("change_voice_tone", "change_voice", "regenerate_audio", "adjust_volume"):
            return [
                EditStep("rerun_audio", "audio", scope, params),
                EditStep("recompose_video", "video", "global"),
            ]
        if name in ("add_background_music",):
            return [
                EditStep("regenerate_bgm", "audio", scope, params),
                EditStep("recompose_video", "video", "global"),
            ]
        if name == "remove_background_music":
            return [
                EditStep("disable_bgm", "audio", "global", params),
                EditStep("recompose_video", "video", "global"),
            ]
        return [EditStep("rerun_audio", "audio", scope, params),
                EditStep("recompose_video", "video", "global")]

    if target == "video_frame":
        # These handlers recompose the video themselves.
        if name in ("apply_filter", "adjust_scene_aesthetic"):
            return [EditStep("apply_filter", "video_frame", scope, params)]
        if name == "change_character_design":
            return [EditStep("regenerate_portraits", "video_frame", scope, params)]
        return [EditStep("regenerate_scene", "video_frame", scope, params)]

    # target == "video"
    if name == "remove_subtitles":
        return [EditStep("recompose_video", "video", "global", {"subtitles": False})]
    if name == "add_subtitles":
        return [EditStep("recompose_video", "video", "global", {"subtitles": True})]
    if name in ("speed_up", "slow_down"):
        factor = float(params.get("factor", 1.5 if name == "speed_up" else 0.75))
        # "slow down 2x" means half speed, not double.
        if (name == "slow_down") == (factor > 1):
            factor = 1 / factor
        return [EditStep("change_speed", "video", scope, {**params, "factor": factor})]
    return [EditStep("recompose_video", "video", "global", params)]
