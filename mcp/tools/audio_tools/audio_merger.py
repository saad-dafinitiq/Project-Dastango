"""Audio merging — build a dialogue track and overlay BGM via ffmpeg.

Two ways to build the dialogue track:
- `placements` (preferred): each clip is placed at its absolute `start_ms` on
  the film timeline, padded/trimmed to `total_ms`. This is what keeps audio in
  sync with video (see shared/timeline.py).
- `segments`: plain back-to-back concatenation (e.g. for joining BGM beds).
"""
from __future__ import annotations
import math
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from mcp.base_tool import BaseTool, ToolResult
from shared.utils.logging import get_logger

log = get_logger("audio_merger")

SAMPLE_RATE = 22050

# The mix (DST-41). Dialogue is the anchor, as in broadcast practice (EBU R128 and ATSC A/85 both
# measure against the dialogue): every line is brought to one loudness, and the music and the
# ambience are set *relative to it*, not by an absolute gain on whatever level the file happens to
# have. Editors keep a music bed some 18 to 25 dB under speech (WCAG 1.4.7 asks for 20); the gap
# here comes mostly from the bed's own level (about 15 dB under a line's loudness, between lines) plus a
# gentle duck (7 dB) while someone speaks, because a deeper duck makes the music pump. Measured on a
# real film (Kokoro voices, library music) by the level over each whole line, pauses included, that is
# 17 to 20 dB under speech (5 to 8 dB before), and about 11 dB between lines.
# These are loudness (LUFS) before the master is normalised, so only the differences matter.
LINE_LUFS = -20.0
MUSIC_REF_LUFS = -35.0         # the music bed at its "100%" (music_volume == MUSIC_BASE)
AMBIENCE_REF_LUFS = -40.0      # an ambience bed at its "100%" (ambience_volume == AMBIENCE_BASE)
MUSIC_BASE = 0.32              # AudioOutput.music_volume "as the film was made"
AMBIENCE_BASE = 0.22           # AudioOutput.ambience_volume "as the film was made"
MAX_LINE_GAIN_DB = 12.0        # a line is never lifted or cut by more than this
MASTER_LUFS = -16.0            # streaming / podcast target for the finished film
MASTER_PEAK_LIMIT = 0.8        # linear, about -1.9 dBFS: keeps inter-sample peaks under -1.5 dBTP
# Ducking is automation written from the timeline, as an editor's auto-duck does: the bed dips by
# DUCK_DB for the length of a line, starting a moment before it, and comes back softly after it.
# (ffmpeg's side-chain compressor was the first choice, but it never ducked a steady voice by more
# than about 5 dB whatever its ratio and threshold, and its depth followed the key's wave, not the line.)
DUCK_DB = 7.0                  # 6 to 12 dB is "gentle"; deeper and the music is heard to pump
DUCK_LEAD_S = 0.12             # the dip starts this long before the line does
DUCK_ATTACK_S = 0.25           # ...and takes this long to reach full depth
DUCK_RELEASE_S = 0.5           # the bed climbs back over this long after the line ends
DUCK_HOLD_S = 0.7              # lines closer than this are one dip, not a flutter
# The side-chain compressor, only for a mix with no timeline (clips joined end to end).
DUCK_THRESHOLD = 0.03
DUCK_RATIO = 4
DUCK_ATTACK_MS = 40
DUCK_RELEASE_MS = 400


def measure_lufs(path: Path) -> Optional[float]:
    """Integrated loudness (ITU-R BS.1770, gated) of a file, or None for silence or a file that
    will not decode."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                           "ebur128=peak=none", "-f", "null", "-"], capture_output=True, text=True)
    tail = proc.stderr.rsplit("Summary:", 1)
    if len(tail) != 2:
        return None
    found = re.search(r"\n\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", tail[1])
    if not found:
        return None
    value = float(found.group(1))
    return value if value > -69.0 else None        # ebur128 answers -70 for silence


def _db(ratio: float) -> float:
    return 20.0 * math.log10(max(ratio, 1e-6))


def duck_spans(lines: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Lines as (start_s, end_s), sorted, with those closer than DUCK_HOLD_S made into one span."""
    merged: List[List[float]] = []
    for start, end in sorted(lines):
        if merged and start - merged[-1][1] < DUCK_HOLD_S:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def duck_expression(spans: List[Tuple[float, float]], depth_db: float = DUCK_DB) -> str:
    """The `volume` expression (per frame, in time t) that dips the bed under each span."""
    floor = 10 ** (-depth_db / 20.0)
    ramps = [f"clip((t-{max(0.0, a - DUCK_LEAD_S):.3f})/{DUCK_ATTACK_S},0,1)*(1-clip((t-{b:.3f})/{DUCK_RELEASE_S},0,1))"
             for a, b in spans]
    deepest = ramps[0]
    for ramp in ramps[1:]:
        deepest = f"max({deepest},{ramp})"
    return f"1-{1.0 - floor:.4f}*({deepest})"


class AudioMergerTool(BaseTool):
    name = "audio.merge"
    description = (
        "Place dialogue clips on a timeline (or concatenate clips) into a master "
        "track and optionally mix in a BGM bed."
    )
    category = "audio"

    def run(self, out_path: str, segments: Optional[List[str]] = None,
            bgm: Optional[str] = None, bgm_volume: float = 0.25,
            placements: Optional[List[Dict]] = None,
            total_ms: Optional[int] = None, duck: bool = True,
            loudness: bool = True, ambience: Optional[str] = None,
            ambience_volume: float = 0.22, dialogue_volume: float = 1.0,
            level_voices: bool = True, anchor_beds: bool = True, **_) -> ToolResult:
        """`level_voices` brings every placed line to one loudness (a placement's `offset_db`
        keeps a deliberate whisper or shout that far from it); `anchor_beds` sets the music and
        ambience relative to that, so the dialogue stays clear however loud the files were."""
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() not in (".wav", ".mp3", ".m4a"):
            out = out.with_suffix(".wav")

        dialogue_path = out.parent / f"{out.stem}_dialogue.wav"
        spans = None            # no timeline (clips joined end to end): the compressor ducks instead
        if placements is not None:
            if total_ms is None:
                return ToolResult(success=False, error="placements require total_ms")
            placed = [dict(p) for p in placements if Path(p["path"]).exists()]
            missing = len(placements) - len(placed)
            if missing:
                log.warning("%d placement clip(s) missing on disk — left silent", missing)
            if level_voices:
                self._level(placed)
            spans = duck_spans([(p["start_ms"] / 1000.0, p["start_ms"] / 1000.0 + self._seconds(p))
                                for p in placed])
            self._place(placed, total_ms, dialogue_path)
            count = len(placed)
        else:
            segs = [s for s in (segments or []) if Path(s).exists()]
            if not segs:
                return ToolResult(success=False, error="no input segments exist")
            self._concat(segs, dialogue_path)
            count = len(segs)

        have_bgm = bool(bgm and Path(bgm).exists())
        have_amb = bool(ambience and Path(ambience).exists())
        if have_bgm or have_amb or (dialogue_volume != 1.0 and placements is not None):
            self._mix(dialogue_path, Path(bgm) if have_bgm else None, out, bgm_volume=bgm_volume,
                      duck=duck, loudness=loudness,
                      ambience=Path(ambience) if have_amb else None,
                      ambience_volume=ambience_volume, dialogue_volume=dialogue_volume,
                      anchor_beds=anchor_beds, duck_spans=spans)
        elif loudness and placements is not None:
            self._normalize_only(dialogue_path, out)
        else:
            # Re-encode straight to the target format.
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(dialogue_path), str(out)],
                check=True, capture_output=True,
            )

        try:
            dialogue_path.unlink()
        except Exception:  # noqa: BLE001
            pass

        return ToolResult(success=True, data=str(out),
                          metadata={"segment_count": count, "bgm": have_bgm, "ambience": have_amb,
                                    "ducked": bool(bgm) and duck, "loudness": loudness,
                                    "mode": "placements" if placements is not None else "concat"})

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _duration(path: Path) -> Optional[float]:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                               "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True)
        try:
            return float(proc.stdout.strip())
        except ValueError:
            return None

    @staticmethod
    def _seconds(placement: Dict) -> float:
        """How long a placed line lasts: the caller's `duration_ms`, else asked of ffprobe."""
        if placement.get("duration_ms"):
            return float(placement["duration_ms"]) / 1000.0
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                               "default=noprint_wrappers=1:nokey=1", str(placement["path"])],
                              capture_output=True, text=True)
        try:
            return float(proc.stdout.strip())
        except ValueError:
            return 0.0

    @staticmethod
    def _level(placements: List[Dict]) -> None:
        """Give each placement a `gain_db` that brings its line to LINE_LUFS, plus the line's own
        `offset_db` (a whispered or shouted tone is meant to sit off the anchor). A line that cannot
        be measured (silent, or too short to gate) is left as it is."""
        for p in placements:
            offset = float(p.get("offset_db") or 0.0)
            heard = measure_lufs(Path(p["path"]))
            if heard is None:
                p["gain_db"] = offset
                continue
            p["gain_db"] = max(-MAX_LINE_GAIN_DB, min(MAX_LINE_GAIN_DB, LINE_LUFS - heard)) + offset

    def _place(self, placements: List[Dict], total_ms: int, out: Path) -> None:
        """Mix clips onto a silent bed of `total_ms`, each at its `start_ms`."""
        total_s = max(0.05, total_ms / 1000.0)
        if not placements:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"anullsrc=channel_layout=mono:sample_rate={SAMPLE_RATE}",
                 "-t", f"{total_s:.3f}", "-c:a", "pcm_s16le", str(out)],
                check=True, capture_output=True,
            )
            return
        cmd = ["ffmpeg", "-y"]
        for p in placements:
            cmd += ["-i", str(p["path"])]
        parts = []
        for i, p in enumerate(placements):
            start = max(0, int(p["start_ms"]))
            gain = f"volume={float(p['gain_db']):.2f}dB," if p.get("gain_db") else ""
            parts.append(
                f"[{i}:a]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono,{gain}"
                f"adelay=delays={start}:all=1[d{i}]"
            )
        labels = "".join(f"[d{i}]" for i in range(len(placements)))
        parts.append(
            f"{labels}amix=inputs={len(placements)}:normalize=0:duration=longest,"
            f"apad=whole_dur={total_s:.3f},atrim=end={total_s:.3f}[out]"
        )
        cmd += ["-filter_complex", ";".join(parts), "-map", "[out]",
                "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_f32le", str(out)]   # float: a lifted line must not clip
        subprocess.run(cmd, check=True, capture_output=True)

    def _concat(self, segs: List[str], out: Path) -> None:
        list_path = out.parent / f"{out.stem}_concat.txt"
        list_path.write_text(
            "\n".join(f"file '{Path(s).resolve().as_posix()}'" for s in segs),
            encoding="utf-8",
        )
        # Decode to PCM WAV first so the concat demuxer never trips on mixed codecs/headers.
        norm_dir = out.parent / "_norm"
        norm_dir.mkdir(exist_ok=True)
        norm_paths: List[Path] = []
        for i, s in enumerate(segs):
            norm = norm_dir / f"seg_{i:03d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", s,
                 "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le",
                 str(norm)],
                check=True, capture_output=True,
            )
            norm_paths.append(norm)

        list_path.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in norm_paths),
            encoding="utf-8",
        )
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_path), "-c", "copy", str(out)],
            check=True, capture_output=True,
        )

        # cleanup
        try:
            for p in norm_paths:
                p.unlink(missing_ok=True)
            norm_dir.rmdir()
            list_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _bed_gain(path: Path, volume: float, ref_lufs: float, base: float) -> str:
        """The `volume` filter value for a bed: its own loudness is measured and brought to
        `ref_lufs` (shifted by how far the creator's slider is from its "100%"), so a bed is as far
        under the dialogue as intended whatever level its file has. Unmeasurable beds fall back to
        the plain multiplier."""
        if volume <= 0:
            return "0"
        heard = measure_lufs(path)
        if heard is None:
            return f"{volume}"
        return f"{ref_lufs + _db(volume / base) - heard:.2f}dB"

    def _mix(self, dialogue: Path, bgm: Optional[Path], out: Path, bgm_volume: float = 0.25,
             duck: bool = True, loudness: bool = True, ambience: Optional[Path] = None,
             ambience_volume: float = 0.22, dialogue_volume: float = 1.0,
             anchor_beds: bool = True, duck_spans: Optional[List[Tuple[float, float]]] = None) -> None:
        """Mix music and ambience under dialogue.

        The dialogue is the anchor (see LINE_LUFS above): with `anchor_beds` the music and
        ambience are measured and set relative to it, so about 12 dB under a line between lines. With
        `duck` the bed also dips DUCK_DB (7 dB) under each line, from the line times (`duck_spans`),
        and comes back softly in the gaps. Together that keeps speech some 18 to 20 dB over the bed. `dialogue_volume` scales the voices before the mix
        (what a creator's "voices louder" changes). `loudness` then normalises the finished film
        to MASTER_LUFS in two passes (exact, not approximate), so the level of the whole film is
        fixed and these volumes are *relative*: more music means the voices sit lower in it.
        """
        inputs = ["-i", str(dialogue)]
        parts = [f"[0:a]volume={dialogue_volume}[voice]"]
        beds = []
        idx = 1
        if bgm:
            gain = (self._bed_gain(bgm, bgm_volume, MUSIC_REF_LUFS, MUSIC_BASE) if anchor_beds
                    else f"{bgm_volume}")
            inputs += ["-i", str(bgm)]
            parts.append(f"[{idx}:a]volume={gain},aloop=loop=-1:size=2e9,"
                         f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono[music]")
            beds.append("[music]")
            idx += 1
        if ambience:
            gain = (self._bed_gain(ambience, ambience_volume, AMBIENCE_REF_LUFS, AMBIENCE_BASE)
                    if anchor_beds else f"{ambience_volume}")
            inputs += ["-i", str(ambience)]
            parts.append(f"[{idx}:a]volume={gain},aloop=loop=-1:size=2e9,"
                         f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono[amb]")
            beds.append("[amb]")
            idx += 1
        if not beds:
            parts.append("[voice]anull[mixed]")
        else:
            if len(beds) == 1:
                parts.append(f"{beds[0]}anull[bed]")
            else:
                parts.append(f"{''.join(beds)}amix=inputs={len(beds)}:duration=longest:normalize=0[bed]")
            if duck and duck_spans is not None:
                if duck_spans:
                    parts.append(f"[bed]volume='{duck_expression(duck_spans)}':eval=frame[ducked]")
                else:
                    parts.append("[bed]anull[ducked]")
                parts.append("[voice][ducked]amix=inputs=2:duration=first:"
                             "dropout_transition=2:normalize=0[mixed]")
            elif duck:
                # One copy of the dialogue drives the compressor, the other is heard.
                parts.append("[voice]asplit=2[dry][key]")
                parts.append(f"[bed][key]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}:"
                             f"attack={DUCK_ATTACK_MS}:release={DUCK_RELEASE_MS}:makeup=1[ducked]")
                parts.append("[dry][ducked]amix=inputs=2:duration=first:"
                             "dropout_transition=2:normalize=0[mixed]")
            else:
                # normalize=0 keeps dialogue at full level instead of halving it.
                parts.append("[voice][bed]amix=inputs=2:duration=first:"
                             "dropout_transition=2:normalize=0[mixed]")

        if not loudness:
            cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts + ["[mixed]anull[a]"]),
                   "-map", "[a]", "-ar", str(SAMPLE_RATE), "-ac", "2", str(out)]
            subprocess.run(cmd, check=True, capture_output=True)
            return
        # Float, so a mix that peaks over full scale is not clipped before it is normalised.
        premaster = out.with_name(f"{out.stem}_premaster.wav")
        try:
            cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts + ["[mixed]anull[a]"]),
                   "-map", "[a]", "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_f32le", str(premaster)]
            subprocess.run(cmd, check=True, capture_output=True)
            self._master(premaster, out)
        finally:
            premaster.unlink(missing_ok=True)

    @staticmethod
    def _master(src: Path, out: Path) -> None:
        """Bring the finished film to MASTER_LUFS with one plain gain, found by measuring it, then a
        peak limiter. ffmpeg's loudnorm was not used: it falls back to a *dynamic* mode whenever the
        film's loudness range is wider than it likes (a quiet bed under loud dialogue is exactly
        that), and then lifts the bed in the gaps, undoing the balance the mix has just set. A
        silent file is only converted."""
        heard = measure_lufs(src)
        if heard is None:
            subprocess.run(["ffmpeg", "-y", "-i", str(src), "-ar", str(SAMPLE_RATE), "-ac", "2", str(out)],
                           check=True, capture_output=True)
            return
        gain = MASTER_LUFS - heard
        length = AudioMergerTool._duration(src)
        # The limiter looks 5 ms ahead and so delays what it limits by 5 ms (found by comparing the mix with the
        # voices it was made from: everything was 109 samples late against the picture). The track is padded by
        # 10 ms before it and the first 5 ms taken off after it, and cut back to its own length.
        # The limiter takes some off the peaks, so the loudness comes out a little under the target and
        # the gain is corrected by what is missing, a few times: a speech mix with high peaks settles
        # within about 0.1 LU in three or four rounds.
        for _ in range(6):
            af = (f"volume={gain:.3f}dB,apad=pad_dur=0.01,"
                  f"alimiter=limit={MASTER_PEAK_LIMIT}:attack=5:release=50:level=0,"
                  f"atrim=start=0.005,asetpts=PTS-STARTPTS")
            cmd = ["ffmpeg", "-y", "-i", str(src), "-af", af, "-ar", str(SAMPLE_RATE), "-ac", "2"]
            if length:
                cmd += ["-t", f"{length:.4f}"]
            subprocess.run([*cmd, str(out)], check=True, capture_output=True)
            got = measure_lufs(out)
            if got is None or abs(got - MASTER_LUFS) < 0.1:
                break
            gain += MASTER_LUFS - got

    def _normalize_only(self, src: Path, out: Path) -> None:
        """No music: still normalise the dialogue to the same target loudness."""
        self._master(src, out)
