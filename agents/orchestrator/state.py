"""Run-time orchestration state — wraps PipelineState with progress events."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from shared import film_format
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
    # DST-34: the creator's two choices; the film's size follows from the format.
    format: str = film_format.DEFAULT_FORMAT
    style: str = film_format.DEFAULT_STYLE
    # The film's size; None = the size of its format, filled in at creation (pictures are drawn smaller).
    width: Optional[int] = None
    height: Optional[int] = None
    fps: int = 24
    snapshot_each_phase: bool = True
    final_description: str = "initial pipeline run"
    error: Optional[str] = None
    # Optional video-tier overrides (None = auto-detect from env).
    use_text_to_video: Optional[bool] = None
    use_lip_sync: Optional[bool] = None

    def __post_init__(self) -> None:
        # The film's size follows from its format unless a size was given (small test films).
        w, h = film_format.film_size(self.format)
        self.width = self.width or w
        self.height = self.height or h

    def film_size(self) -> tuple:
        return (self.width, self.height)
