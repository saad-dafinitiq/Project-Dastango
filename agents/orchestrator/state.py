"""Run-time orchestration state — wraps PipelineState with progress events."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from shared.constants import DEFAULT_HEIGHT, DEFAULT_WIDTH
from shared.schemas.pipeline import PipelineState


@dataclass
class RunContext:
    """Per-run context passed through the orchestrator graph."""
    state: PipelineState
    target_duration_s: int = 45
    scene_count: int = 4
    with_bgm: bool = True
    # None = whatever config/providers.yaml picks for the tts role.
    tts_engine: Optional[str] = None
    with_subtitles: bool = True
    subtitle_language: str = "English"
    burn_subtitles: bool = True
    width: int = DEFAULT_WIDTH           # the film's size; pictures are drawn smaller
    height: int = DEFAULT_HEIGHT
    fps: int = 24
    snapshot_each_phase: bool = True
    final_description: str = "initial pipeline run"
    error: Optional[str] = None
    # Optional video-tier overrides (None = auto-detect from env).
    use_text_to_video: Optional[bool] = None
    use_lip_sync: Optional[bool] = None
