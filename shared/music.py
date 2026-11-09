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
SAMPLE_RATE = 22050

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

# Ambience, drawn by ffmpeg from noise: kind -> label, about, a filter on a noise source and a gain
# in dB that brings every kind to the music's own level (about -14 dB mean), so that 100% of
# the ambience is audible beside 100% of the music whichever kind it is.
AMBIENCE: Dict[str, Dict[str, str]] = {
    "rain": {"label": "Rain", "about": "steady rain on a roof",
             "src": "anoisesrc=color=pink:amplitude=0.9",
             "af": "highpass=f=700,lowpass=f=9000,tremolo=f=9:d=0.18", "gain": "8"},
    "wind": {"label": "Wind", "about": "wind across open ground",
             "src": "anoisesrc=color=brown:amplitude=0.9",
             "af": "highpass=f=80,lowpass=f=1100,tremolo=f=0.23:d=0.55", "gain": "8"},
    "city": {"label": "City", "about": "far-off traffic and a busy street",
             "src": "anoisesrc=color=brown:amplitude=0.9",
             "af": "highpass=f=100,lowpass=f=2200,tremolo=f=0.7:d=0.3", "gain": "6.5"},
    "sea": {"label": "Sea", "about": "waves on a shore",
            "src": "anoisesrc=color=pink:amplitude=0.9",
            "af": "highpass=f=120,lowpass=f=2400,tremolo=f=0.11:d=0.7", "gain": "10"},
    "night": {"label": "Night", "about": "a still night with insects",
              "src": "anoisesrc=color=brown:amplitude=0.25",
              "af": "highpass=f=3500,lowpass=f=6500,tremolo=f=11:d=0.9", "gain": "37"},
}

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
    """`duration_ms` of the track, from `offset_ms` into it (the track repeats), as 22.05 kHz mono."""
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
          f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono")
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
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=channel_layout=mono:sample_rate={SAMPLE_RATE}",
          "-t", f"{max(0.05, duration_ms / 1000.0):.3f}", "-c:a", "pcm_s16le", str(out)])
    return out


def ambience_bed(kind: str, duration_ms: int, out: Path, fade_ms: int = 300,
                 fade_in_ms: Optional[int] = None, fade_out_ms: Optional[int] = None) -> Path:
    """`duration_ms` of an ambience, 22.05 kHz mono. `fade_ms` fades both ends; `fade_in_ms` and
    `fade_out_ms` say each end on its own (0 is none, for a bed that carries straight on)."""
    spec = AMBIENCE.get(kind)
    if not spec:
        raise MusicError(f"unknown ambience {kind!r}: {', '.join(AMBIENCE)}")
    dur = max(0.05, duration_ms / 1000.0)
    fi = min((fade_ms if fade_in_ms is None else fade_in_ms) / 1000.0, dur / 3)
    fo = min((fade_ms if fade_out_ms is None else fade_out_ms) / 1000.0, dur / 3)
    # The limiter looks ahead 5 ms and so starts every bed with 5 ms of silence: draw 10 ms more and cut the
    # first 5 ms off (DST-41), or a bed that carries on across a scene cut begins with a gap.
    af = (f"{spec['af']},volume={spec['gain']}dB,alimiter=limit=0.9,atrim=start=0.005,asetpts=PTS-STARTPTS,"
          f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono")
    if fi > 0:       # none where the same ambience carries straight on (see cut_bed)
        af += f",afade=t=in:st=0:d={fi:.3f}"
    if fo > 0:
        af += f",afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}"
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"{spec['src']}:sample_rate={SAMPLE_RATE}:duration={dur + 0.01:.3f}",
          "-af", af, "-t", f"{dur:.3f}", "-c:a", "pcm_s16le", str(out)])
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
    big, one that will not decode, a video, or a length outside 3 s to 10 min. The copy is mono
    MP3 at -16 LUFS (what the film's own music is), so an upload is not louder than the library.
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
        _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "32000",
              "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "libmp3lame", "-b:a", "96k", str(out)])
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
