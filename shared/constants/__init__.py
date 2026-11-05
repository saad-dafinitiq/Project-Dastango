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
# The finished film's size (DST-35): 1080p. The pictures it is cut from are drawn
# smaller (config/video.yaml, images.long_side: shared/video_quality.image_size)
# and enlarged once, by the camera. A film saved before this keeps its own size.
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_SAMPLE_RATE = 22050

# How far a film may be from the length asked for, as a fraction of the target,
# before it is flagged to the creator instead of reported as a plain success. It
# is the product's promise (you describe the film, you get that film), proposed
# to Hashim on 29 Oct as +/-15%, and DST-28 holds scripts and finished films to
# it: a script outside it is repaired (below), a film outside it is flagged.
# History: DST-17 set 0.5, because the DST-10 baseline (2026-10-29) had normal
# films 0.4% to 42% short (so 0.15 would have flagged 6 of 7) and one broken
# film (dst-05, dialogue the voice engine could not read) 5x its target; 0.5 was
# the line that caught only the broken one. FILM_LENGTH_TOLERANCE in .env overrides it.
FILM_LENGTH_TOLERANCE = 0.15

# How many times a script outside the tolerance is sent back to the model with
# "add or cut N words". Each repair is a full extra script call on a free tier
# (Groq's tokens-per-minute limit refused a plain retry in 3 of the DST-10
# plans), so it is capped; two lets a second try correct the first one's
# overshoot. SCRIPT_LENGTH_REPAIRS in .env overrides it (0 turns repairs off).
SCRIPT_LENGTH_REPAIRS = 2

# How many times a script whose cast has a character the creator named in their
# prompt but who has no lines is sent back to the model to give them some (DST-33).
# One: a character who was asked for is worth one more call, a second try is
# rarely different, and each repair is a full script call on a free tier.
# SCRIPT_CAST_REPAIRS in .env overrides it (0 turns the repair off; the trim still runs).
SCRIPT_CAST_REPAIRS = 1


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


def script_length_repairs() -> int:
    """SCRIPT_LENGTH_REPAIRS from the environment, read when a script is written."""
    raw = os.getenv("SCRIPT_LENGTH_REPAIRS", "").strip()
    try:
        value = int(raw) if raw else SCRIPT_LENGTH_REPAIRS
    except ValueError:
        value = -1
    if not 0 <= value <= 5:
        from shared.utils.logging import get_logger
        get_logger("constants").warning(
            "SCRIPT_LENGTH_REPAIRS=%r is not a whole number from 0 to 5; using %s",
            raw, SCRIPT_LENGTH_REPAIRS)
        return SCRIPT_LENGTH_REPAIRS
    return value


def script_cast_repairs() -> int:
    """SCRIPT_CAST_REPAIRS from the environment, read when a script is written."""
    raw = os.getenv("SCRIPT_CAST_REPAIRS", "").strip()
    try:
        value = int(raw) if raw else SCRIPT_CAST_REPAIRS
    except ValueError:
        value = -1
    if not 0 <= value <= 3:
        from shared.utils.logging import get_logger
        get_logger("constants").warning(
            "SCRIPT_CAST_REPAIRS=%r is not a whole number from 0 to 3; using %s",
            raw, SCRIPT_CAST_REPAIRS)
        return SCRIPT_CAST_REPAIRS
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
