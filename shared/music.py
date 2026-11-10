"""The film's music and ambience (DST-39): a library of tracks, uploads, and the beds under a scene.

Music comes from three places, in this order for any scene:
  1. what the creator chose for that scene (`AudioOutput.scene_music`; "none" is silence),
  2. what they chose for the whole film (`AudioOutput.music_track`),
  3. the library track for the scene's mood (`Scene.music_mood`), varied by position so two
     scenes of one mood do not always get the same file.
A choice is a library id ("tense-1") or an upload ("upload:<id>").

Tracks are cut to the scene from where the film's clock is, so consecutive scenes on one track
play on continuously instead of restarting, and a change of track crossfades. Everything is
ffmpeg; nothing here knows about the pipeline except `AudioOutput`'s fields.
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from shared.utils.logging import get_logger

log = get_logger("music")

LIBRARY_DIR = Path(__file__).resolve().parent.parent / "assets" / "music"
# The audio chain runs at CD rate in stereo (DST-42). It was 22.05 kHz mono, which cut everything above
# 11 kHz from every film. The voices cannot add what they never had (Kokoro and Edge speak at 24 kHz), but
# the music and the ambience can use the band, and a stereo bed under a centred voice is how a film is
# mixed. Every bed is written in this one format so the concat demuxer can join them without decoding.
SAMPLE_RATE = 44100
CHANNELS = "stereo"

# What a writing model calls a mood vs. the ten the library has.
ALIASES = {
    "melancholic": "sad", "melancholy": "sad", "sorrowful": "sad", "somber": "sad", "wistful": "sad",
    "happy": "joyful", "cheerful": "joyful", "playful": "joyful", "warm": "joyful", "hopeful": "joyful",
    "suspenseful": "tense", "anxious": "tense", "thriller": "tense", "dramatic": "tense",
    "dark": "ominous", "sinister": "ominous", "eerie": "ominous", "menacing": "ominous",
    "heroic": "epic", "grand": "epic", "triumphant": "epic", "cinematic": "epic",
    "lively": "energetic", "upbeat": "energetic", "exciting": "energetic", "action": "energetic",
    "dreamy": "ethereal", "magical": "ethereal", "serene": "ethereal", "peaceful": "ambient",
    "calm": "ambient", "quiet": "ambient", "mystery": "mysterious", "curious": "mysterious",
}

# Ambience, drawn by ffmpeg from noise: kind -> label, about, the noise, a filter, and a gain in dB that
# brings every kind to the same loudness (AMBIENCE_LUFS, measured with ebur128 on a ten second bed), so that
# 100% of the ambience is as loud as 100% of any other kind. DST-42: they were matched by ear and by RMS, and
# rain and night came out about 4 LU louder than wind and city. Two things changed with the rate: the beds are
# stereo (two noise sources with different seeds, one per ear, so wind and sea are wide instead of a line down
# the middle), and the energy of rain, night and sea is out of the 2 to 5 kHz band where consonants live: rain
# has a cut there and keeps its hiss above it, night is a band of insects well above it (it sat at 3.5 to
# 6.5 kHz, 63% of its energy in the speech band), and the sea is lower. A test measures both.
AMBIENCE_LUFS = -22.0
AMBIENCE: Dict[str, Dict[str, str]] = {
    "rain": {"label": "Rain", "about": "steady rain on a roof",
             "src": "anoisesrc=color=pink:amplitude=0.9",
             "af": "highpass=f=500,lowpass=f=14000,equalizer=f=3300:t=o:w=2.5:g=-14,tremolo=f=9:d=0.18", "gain": "-1.2"},
    "wind": {"label": "Wind", "about": "wind across open ground",
             "src": "anoisesrc=color=brown:amplitude=0.9",
             "af": "highpass=f=80,lowpass=f=1100,tremolo=f=0.23:d=0.55", "gain": "-5.1"},
    "city": {"label": "City", "about": "far-off traffic and a busy street",
             "src": "anoisesrc=color=brown:amplitude=0.9",
             "af": "highpass=f=100,lowpass=f=2200,tremolo=f=0.7:d=0.3", "gain": "-6.3"},
    "sea": {"label": "Sea", "about": "waves on a shore",
            "src": "anoisesrc=color=pink:amplitude=0.9",
            "af": "highpass=f=120,lowpass=f=1500,tremolo=f=0.11:d=0.7", "gain": "-1.7"},
    "night": {"label": "Night", "about": "a still night with insects",
              "src": "anoisesrc=color=white:amplitude=0.5",
              "af": "highpass=f=6500,lowpass=f=14000,tremolo=f=11:d=0.9", "gain": "-9.5"},
}
# One seed per ear; fixed, so a bed is the same every time it is drawn.
AMBIENCE_SEEDS = (11, 23)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
UPLOAD_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")
MIN_UPLOAD_S, MAX_UPLOAD_S = 3.0, 600.0


class MusicError(ValueError):
    """Something about a track or an upload that the creator can fix."""


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    mood: str
    file: str
    duration_s: float
    licence: str = "CC0 1.0"
    author: str = ""

    @property
    def path(self) -> Path:
        return LIBRARY_DIR / self.file


@lru_cache(maxsize=1)
def library() -> Dict[str, Track]:
    """Every track in assets/music/library.json whose file is there."""
    meta = LIBRARY_DIR / "library.json"
    if not meta.exists():
        return {}
    out: Dict[str, Track] = {}
    for row in json.loads(meta.read_text(encoding="utf-8")).get("tracks", []):
        t = Track(row["id"], row["title"], row["mood"], row["file"], float(row["duration_s"]),
                  row.get("licence", "CC0 1.0"), row.get("author", ""))
        if t.path.exists():
            out[t.id] = t
    return out


def moods() -> List[str]:
    return sorted({t.mood for t in library().values()})


def resolve_mood(mood: Optional[str]) -> str:
    """The library mood closest to what a script says ('melancholic' -> 'sad'); 'ambient' if none."""
    key = (mood or "").lower().strip()
    if key in moods():
        return key
    return ALIASES.get(key, "ambient")


def for_mood(mood: Optional[str], index: int = 0) -> Optional[Track]:
    """A track of the mood, the variant chosen by the scene's position."""
    pool = sorted((t for t in library().values() if t.mood == resolve_mood(mood)), key=lambda t: t.id)
    return pool[index % len(pool)] if pool else None


def catalogue(uploads: Optional[list] = None) -> List[dict]:
    """What the interface may offer: the library and this film's own uploads."""
    rows = [{"id": t.id, "title": t.title, "mood": t.mood, "seconds": t.duration_s,
             "licence": t.licence, "upload": False} for t in sorted(library().values(), key=lambda t: t.id)]
    for u in uploads or []:
        rows.append({"id": f"upload:{u.id}", "title": u.title, "mood": None,
                     "seconds": u.duration_s, "licence": "yours", "upload": True})
    return rows


# ---- choosing the music for a scene -------------------------------------------------------

def track_path(choice: str, uploads: list) -> Path:
    """The file behind a choice ('tense-1' or 'upload:ab12'); MusicError if there is none."""
    if choice.startswith("upload:"):
        found = next((u for u in uploads if f"upload:{u.id}" == choice), None)
        if not found or not Path(found.path).exists():
            raise MusicError(f"there is no uploaded track {choice!r} in this film")
        return Path(found.path)
    t = library().get(choice)
    if not t:
        raise MusicError(f"there is no track {choice!r}; the library has {', '.join(sorted(library()))}")
    return t.path


def choice_for_scene(audio, scene, index: int) -> Optional[str]:
    """The track id this scene plays, or None for silence."""
    picked = audio.scene_music.get(scene.scene_id)
    if picked == "none":
        return None
    picked = picked or audio.music_track
    if picked:
        return picked
    t = for_mood(scene.music_mood, index)
    return t.id if t else None


def track_seconds(choice: str, uploads: list) -> float:
    if choice.startswith("upload:"):
        return next((u.duration_s for u in uploads if f"upload:{u.id}" == choice), 0.0)
    t = library().get(choice)
    return t.duration_s if t else 0.0


# ---- cutting beds -------------------------------------------------------------------------

def _run(cmd: List[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def cut_bed(src: Path, offset_ms: int, duration_ms: int, out: Path,
            fade_in_ms: int = 20, fade_out_ms: int = 20) -> Path:
    """`duration_ms` of the track, from `offset_ms` into it (the track repeats), as 44.1 kHz stereo (a mono
    track is the same in both ears; an uploaded stereo track keeps its two)."""
    dur = max(0.05, duration_ms / 1000.0)
    fi, fo = fade_in_ms / 1000.0, fade_out_ms / 1000.0
    start = offset_ms / 1000.0
    # The bed is cut inside the filter chain, by sample, and the clock is reset, so the fades are timed
    # from the start of the bed. Two earlier ways went wrong. Seeking on the output left the clock at the
    # offset, so a scene that began 18 s into a track had already "faded out" and came out silent. Seeking
    # on the input (`-ss` before `-i`) made the MP3 decoder start cold: every bed from a non-zero offset began
    # with 43 to 68 ms of digital silence, which with the fades was the hole heard at every scene cut (DST-41).
    # A fade of 0 is no fade: where one bed carries straight on into the next (the same track across a cut)
    # the two meet with nothing between them.
    af = (f"atrim=start={start:.4f}:end={start + dur:.4f},asetpts=PTS-STARTPTS,"
          f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts={CHANNELS}")
    if fi > 0:
        af += f",afade=t=in:st=0:d={fi:.3f}"
    if fo > 0:
        af += f",afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}"
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src),
          "-t", f"{dur:.3f}", "-af", af, "-c:a", "pcm_s16le", str(out)])
    return out


def silence(duration_ms: int, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=channel_layout={CHANNELS}:sample_rate={SAMPLE_RATE}",
          "-t", f"{max(0.05, duration_ms / 1000.0):.3f}", "-c:a", "pcm_s16le", str(out)])
    return out


def ambience_bed(kind: str, duration_ms: int, out: Path, fade_ms: int = 300,
                 fade_in_ms: Optional[int] = None, fade_out_ms: Optional[int] = None,
                 offset_ms: int = 0) -> Path:
    """`duration_ms` of an ambience, 44.1 kHz stereo, taken from `offset_ms` into the film's own ambience.
    `fade_ms` fades both ends; `fade_in_ms` and `fade_out_ms` say each end on its own (0 is none, for a
    bed that carries straight on).

    The noise is seeded and the slow swell (the tremolo) runs on the film's clock, so the bed for a scene
    that starts at 12 s is the very seconds 12 to 12 + duration of one continuous sound: two scenes of the
    same ambience meet with the wind or the sea at the same point of its swell, instead of each scene
    starting its swell again from the beginning (DST-42)."""
    spec = AMBIENCE.get(kind)
    if not spec:
        raise MusicError(f"unknown ambience {kind!r}: {', '.join(AMBIENCE)}")
    dur = max(0.05, duration_ms / 1000.0)
    fi = min((fade_ms if fade_in_ms is None else fade_in_ms) / 1000.0, dur / 3)
    fo = min((fade_ms if fade_out_ms is None else fade_out_ms) / 1000.0, dur / 3)
    # Two noise sources, one per ear, through the same filter. The limiter looks ahead 5 ms and so starts every
    # bed with 5 ms of silence: draw 10 ms more and cut the first 5 ms off (DST-41), or a bed that carries on
    # across a scene cut begins with a gap.
    one = f"{spec['af']},volume={spec['gain']}dB"
    skip = max(0, offset_ms) / 1000.0 + 0.005
    af = (f"[0:a]{one}[l];[1:a]{one}[r];[l][r]amerge=inputs=2,alimiter=limit=0.9,atrim=start={skip:.4f},"
          f"asetpts=PTS-STARTPTS,aformat=sample_rates={SAMPLE_RATE}:channel_layouts={CHANNELS}")
    if fi > 0:       # none where the same ambience carries straight on (see cut_bed)
        af += f",afade=t=in:st=0:d={fi:.3f}"
    if fo > 0:
        af += f",afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y"]
    for seed in AMBIENCE_SEEDS:
        cmd += ["-f", "lavfi", "-i", f"{spec['src']}:sample_rate={SAMPLE_RATE}:duration={skip + dur + 0.01:.3f}:seed={seed}"]
    _run([*cmd, "-filter_complex", af, "-t", f"{dur:.3f}", "-c:a", "pcm_s16le", str(out)])
    return out


def join(parts: List[Path], out: Path) -> Path:
    """Beds end to end (they are all PCM at one rate, so the concat demuxer joins them exactly)."""
    lst = out.with_suffix(".txt")
    lst.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in parts), encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    lst.unlink(missing_ok=True)
    return out


# ---- uploads ------------------------------------------------------------------------------

def _probe(path: Path) -> dict:
    res = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True)
    if res.returncode != 0:
        raise MusicError("that file could not be read as audio")
    return json.loads(res.stdout or "{}")


def store_upload(src: Path, filename: str, dest_dir: Path) -> dict:
    """Check an uploaded file and keep a normalised copy; returns {id, title, path, duration_s}.

    Refused with a reason the creator can act on: a name that is not audio, a file that is too
    big, one that will not decode, a video, or a length outside 3 s to 10 min. The copy is an MP3 at
    44.1 kHz and -16 LUFS (what the film's own music is), so an upload is not louder than the library;
    a stereo upload stays stereo (DST-42: it was folded to mono at 32 kHz and 96 kbps).
    """
    name = Path(filename or "").name
    if Path(name).suffix.lower() not in UPLOAD_EXTENSIONS:
        raise MusicError(f"use an audio file: {', '.join(UPLOAD_EXTENSIONS)}")
    size = src.stat().st_size
    if size == 0:
        raise MusicError("that file is empty")
    if size > MAX_UPLOAD_BYTES:
        raise MusicError(f"that file is {size / 1e6:.1f} MB; the limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    info = _probe(src)
    streams = info.get("streams", [])
    if any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1
           for s in streams):
        raise MusicError("that is a video file; upload the audio only")
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise MusicError("that file has no audio in it")
    seconds = float(info.get("format", {}).get("duration") or 0)
    if seconds < MIN_UPLOAD_S:
        raise MusicError(f"that is {seconds:.1f} s long; use at least {MIN_UPLOAD_S:.0f} s")
    if seconds > MAX_UPLOAD_S:
        raise MusicError(f"that is {seconds / 60:.0f} minutes long; the limit is {MAX_UPLOAD_S / 60:.0f}")
    uid = uuid.uuid4().hex[:8]
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{uid}.mp3"
    try:
        channels = next((int(s.get("channels") or 1) for s in streams if s.get("codec_type") == "audio"), 1)
        stereo = channels >= 2
        _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-map", "0:a:0", "-ac", "2" if stereo else "1",
              "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(SAMPLE_RATE),
              "-c:a", "libmp3lame", "-b:a", "192k" if stereo else "128k", str(out)])
    except subprocess.CalledProcessError:
        out.unlink(missing_ok=True)
        raise MusicError("that file could not be decoded") from None
    title = Path(name).stem[:60] or "Uploaded track"
    record = {"id": uid, "title": title, "path": str(out), "duration_s": round(seconds, 2)}
    # A sidecar, so the edit that uses the track can find it: the upload itself changes no version.
    (dest_dir / f"{uid}.json").write_text(json.dumps(record), encoding="utf-8")
    return record


def uploads_dir(project_id: str) -> Path:
    from shared.utils.files import project_dir
    return project_dir(project_id) / "audio" / "uploads"


def load_upload(project_id: str, uid: str) -> dict:
    """An upload this project stored (see store_upload); MusicError if there is none."""
    if not re.fullmatch(r"[0-9a-f]{8}", uid or ""):
        raise MusicError("that is not an uploaded track")
    side = uploads_dir(project_id) / f"{uid}.json"
    if not side.exists():
        raise MusicError("that uploaded track is not in this film")
    record = json.loads(side.read_text(encoding="utf-8"))
    if not Path(record["path"]).exists():
        raise MusicError("that uploaded track's file is gone")
    return record
