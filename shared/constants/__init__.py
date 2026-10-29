"""Project-wide constants and paths."""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
# Everything a deployment keeps — films, versions, the database — lives here.
# DATA_DIR moves it (a container volume, a data disk, a throwaway copy).
DATA_DIR = Path(os.getenv("DATA_DIR") or ROOT_DIR / "data").resolve()
OUTPUTS_DIR = DATA_DIR / "outputs"
TEMP_DIR = DATA_DIR / "temp"
STATE_DIR = DATA_DIR / "state_versions"
DB_PATH = DATA_DIR / "state.db"

DEFAULT_FPS = 24
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_SAMPLE_RATE = 22050

# A finished film further than this from its target length, as a fraction of the
# target, is flagged to the creator rather than reported as a plain success
# (DST-17). Why 0.5: in the DST-10 baseline (2026-10-29) normal films came out
# 0.4% to 42% short and none was long, while a broken one (dst-05, dialogue the
# voice engine could not read) was 5x its target. 0.5 flags the broken film and
# none of the normal ones. FILM_LENGTH_TOLERANCE in .env overrides it.
FILM_LENGTH_TOLERANCE = 0.5


def film_length_tolerance() -> float:
    """FILM_LENGTH_TOLERANCE from the environment, read when a film finishes."""
    raw = os.getenv("FILM_LENGTH_TOLERANCE", "").strip()
    try:
        value = float(raw) if raw else FILM_LENGTH_TOLERANCE
    except ValueError:
        value = -1.0
    if not 0 < value < 10:     # also catches nan and inf
        from shared.utils.logging import get_logger
        get_logger("constants").warning(
            "FILM_LENGTH_TOLERANCE=%r is not a fraction above 0; using %s",
            raw, FILM_LENGTH_TOLERANCE)
        return FILM_LENGTH_TOLERANCE
    return value

# Phase names
PHASE_STORY = "story"
PHASE_AUDIO = "audio"
PHASE_VIDEO = "video"
PHASE_EDIT = "edit"

# Edit targets
TARGET_AUDIO = "audio"
TARGET_VIDEO_FRAME = "video_frame"
TARGET_VIDEO = "video"
TARGET_SCRIPT = "script"

for d in (DATA_DIR, OUTPUTS_DIR, TEMP_DIR, STATE_DIR):
    d.mkdir(parents=True, exist_ok=True)
