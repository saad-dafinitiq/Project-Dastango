"""Compositor — concatenate scene clips with optional crossfade transitions."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import List, Optional

from mcp.base_tool import BaseTool, ToolResult
from shared import video_quality, xfade
from shared import procs


class CompositorTool(BaseTool):
    name = "video.compose"
    description = (
        "Concatenate per-scene clips into a final MP4 with optional fade transitions and master audio."
    )
    category = "video"

    def run(self, clips: List[str], out_path: str,
            audio_path: Optional[str] = None,
            transition: str = "fade", transition_ms: float = 400,
            durations_s: Optional[List[float]] = None,
            quality: str = "final",
            boundaries: Optional[List[xfade.Boundary]] = None,
            **_) -> ToolResult:
        """`durations_s`, when given, are the exact clip lengths used to place
        crossfades (otherwise each clip is probed).

        `quality` is "final" when this is the last time the film is encoded, and
        "intermediate" when a later pass (burned subtitles, a speed change) will
        encode it again: only the last pass should spend the delivered quality."""
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() != ".mp4":
            out = out.with_suffix(".mp4")

        missing = [c for c in clips if not Path(c).exists()]
        if missing:
            return ToolResult(success=False, error=f"missing input clips: {missing}")
        if not clips:
            return ToolResult(success=False, error="no input clips found")

        if boundaries is not None and len(boundaries) == len(clips) - 1 and len(clips) > 1:
            # DST-39: every scene boundary has its own transition (shared/xfade.py).
            return self._compose_boundaries(clips, out, audio_path, boundaries, durations_s, quality)

        if transition == "cut" or len(clips) == 1:
            self._concat_demuxer(clips, out, audio_path)
            return ToolResult(success=True, data=str(out),
                              metadata={"clip_count": len(clips), "transition": "cut"})

        # Crossfade chain.
        try:
            self._xfade_chain(clips, out, audio_path, transition_ms / 1000.0,
                              durations_s=durations_s, quality=quality)
            return ToolResult(success=True, data=str(out),
                              metadata={"clip_count": len(clips), "transition": transition})
        except subprocess.CalledProcessError as e:
            # Fall back to simple concatenation.
            self._concat_demuxer(clips, out, audio_path)
            return ToolResult(success=True, data=str(out),
                              metadata={"clip_count": len(clips), "transition": "cut",
                                        "fallback": str(e)})

    # ---- helpers ---------------------------------------------------------

    def _compose_boundaries(self, clips: List[str], out: Path, audio: Optional[str],
                            boundaries: List[xfade.Boundary], durations_s: Optional[List[float]],
                            quality: str) -> ToolResult:
        """Join the scenes with a transition per boundary; all cuts need no re-encode."""
        if xfade.all_cuts(boundaries):
            self._concat_demuxer(clips, out, audio)
            return ToolResult(success=True, data=str(out),
                              metadata={"clip_count": len(clips), "transition": "cut"})
        durations = list(durations_s or [])
        if len(durations) != len(clips):
            durations = [self._probe(c) for c in clips]
        fps_, size = xfade.probe_video(clips[0])
        fc, last = xfade.build_graph(durations, boundaries, fps=fps_, size=size)
        inputs: List[str] = []
        for c in clips:
            inputs += ["-i", str(c)]
        has_audio = bool(audio and Path(audio).exists())
        if has_audio:
            inputs += ["-i", str(audio)]
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", f"[{last}]"]
        if has_audio:
            cmd += ["-map", f"{len(clips)}:a:0", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-an"]
        # The film is exactly as long as its timeline: the scenes' frames less the overlaps. xfade offsets
        # are floating-point seconds, and on a long film they can round up to one frame more, so say how
        # many frames there are rather than trust the arithmetic.
        fps = self._fps(clips[0])
        if fps:
            cmd += ["-frames:v", str(round((sum(durations) - sum(d for _n, d in boundaries)) * fps))]
        cmd += [*video_quality.x264_args(quality), str(out)]
        try:
            procs.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            return ToolResult(success=False, error=e.stderr.decode("utf-8", "replace")[-1500:]
                              if isinstance(e.stderr, bytes) else str(e))
        return ToolResult(success=True, data=str(out),
                          metadata={"clip_count": len(clips),
                                    "transition": sorted({n or "cut" for n, _ in boundaries})})

    @staticmethod
    def _fps(path: str) -> float:
        r = procs.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True)
        num, _, den = (r.stdout.strip() or "0/1").partition("/")
        return float(num) / float(den or 1) if float(den or 1) else 0.0

    @staticmethod
    def _probe(path: str) -> float:
        r = procs.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            check=True, capture_output=True, text=True)
        return float(r.stdout.strip() or "0")

    def _concat_demuxer(self, clips: List[str], out: Path, audio: Optional[str]) -> None:
        list_path = out.parent / f"{out.stem}_concat.txt"
        list_path.write_text(
            "\n".join(f"file '{Path(c).resolve().as_posix()}'" for c in clips),
            encoding="utf-8",
        )
        tmp = out.parent / f"{out.stem}_video.mp4"
        procs.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_path), "-c", "copy", str(tmp)],
            check=True, capture_output=True,
        )
        if audio and Path(audio).exists():
            procs.run(
                ["ffmpeg", "-y", "-i", str(tmp), "-i", str(audio),
                 "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                 "-shortest", str(out)],
                check=True, capture_output=True,
            )
            tmp.unlink(missing_ok=True)
        else:
            if tmp.resolve() != out.resolve():
                tmp.replace(out)
        list_path.unlink(missing_ok=True)

    def _xfade_chain(self, clips: List[str], out: Path,
                     audio: Optional[str], xfade_s: float,
                     durations_s: Optional[List[float]] = None,
                     quality: str = "final") -> None:
        durations: List[float] = list(durations_s or [])
        if len(durations) != len(clips):
            # Probe each clip's duration.
            durations = []
            for c in clips:
                r = procs.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(c)],
                    check=True, capture_output=True, text=True,
                )
                durations.append(float(r.stdout.strip() or "0"))

        # Build xfade graph.
        inputs: List[str] = []
        for c in clips:
            inputs += ["-i", str(c)]
        filter_parts = []
        prev_label = "0:v"
        offset = 0.0
        for i in range(1, len(clips)):
            offset += durations[i - 1] - xfade_s
            out_label = f"v{i}"
            filter_parts.append(
                f"[{prev_label}][{i}:v]xfade=transition=fade:"
                f"duration={xfade_s:.6f}:offset={max(0.0, offset):.6f}[{out_label}]"
            )
            prev_label = out_label
        filter_complex = ";".join(filter_parts)
        last = prev_label

        cmd = ["ffmpeg", "-y", *inputs]
        if audio and Path(audio).exists():
            cmd += ["-i", str(audio)]
        cmd += ["-filter_complex", filter_complex, "-map", f"[{last}]"]
        if audio and Path(audio).exists():
            cmd += ["-map", f"{len(clips)}:a:0", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-an"]
        cmd += [*video_quality.x264_args(quality), str(out)]
        procs.run(cmd, check=True, capture_output=True)
