"""Run a fixed set of prompts and measure the result.

Gives comparable numbers across changes and providers: how long each phase
took, whether the film hit its target length, whether audio and video stayed
in sync, which provider actually served each image, and what fell back.

    python scripts/benchmark.py --offline            # fast, no network
    python scripts/benchmark.py --duration 30        # with the real providers
    python scripts/benchmark.py --out docs/BENCHMARK.md
    python scripts/benchmark.py --testset eval/testset.jsonl --render-subset

Results are also written to data/benchmarks/<timestamp>.json.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Same credentials the app uses (tests set PIPELINE_SKIP_DOTENV=1 to opt out).
if os.getenv("PIPELINE_SKIP_DOTENV") != "1":
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:  # noqa: BLE001
        pass

PROMPTS = [
    "A young astronaut discovers a hidden ocean on Mars",
    "A lighthouse keeper befriends a stranded whale",
    "A night librarian finds the books rearranging into a map",
]

# Every line of a test set (eval/testset.jsonl) has these; eval/README.md explains each.
TESTSET_FIELDS = ("id", "prompt", "target_duration_s", "expected_scenes",
                  "expected_characters", "subtitle_language", "tags", "render_subset")


def _offline() -> None:
    """Force the offline providers so a benchmark can run anywhere."""
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["PROVIDER_IMAGE"] = "placeholder"
    os.environ["PROVIDER_TTS"] = "silent"


def _video_frames(path: str) -> int:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return int(json.loads(probe.stdout)["streams"][0]["nb_read_frames"])


def _phase_seconds(state, phase: str) -> float:
    block = getattr(state, phase)
    if not block.started_at or not block.finished_at:
        return 0.0
    started = datetime.fromisoformat(block.started_at)
    finished = datetime.fromisoformat(block.finished_at)
    return round((finished - started).total_seconds(), 1)


def measure(state, target_s: int, elapsed: float, providers_used: Counter,
            preferred_image: str = "") -> Dict[str, Any]:
    """Everything worth comparing between runs, from one finished project."""
    from shared.timeline import ms_to_frame

    manifest = state.audio.manifest
    video = state.video
    shots = [s for f in video.frames for s in f.shots]
    cut_starts = {s.start_ms for s in shots}
    expected_frames = ms_to_frame(manifest.total_duration_ms / video.speed_factor, video.fps)
    actual_frames = _video_frames(video.final_video_path)
    film_s = manifest.total_duration_ms / 1000

    return {
        "project_id": state.project_id,
        "scenes": len(state.script.scenes),
        "lines": len(manifest.segments),
        "shots": len(shots),
        "target_s": target_s,
        "film_s": round(film_s, 1),
        "duration_error_pct": round(abs(film_s - target_s) / target_s * 100, 1),
        "frames_expected": expected_frames,
        "frames_actual": actual_frames,
        "in_sync": actual_frames == expected_frames,
        "lines_on_a_cut": sum(seg.start_ms in cut_starts for seg in manifest.segments),
        "subtitle_languages": video.subtitle_languages,
        "image_providers": dict(providers_used),
        # Images the preferred provider didn't serve (it failed and the chain moved on).
        "fallback_images": sum(n for provider, n in providers_used.items()
                               if preferred_image and provider != preferred_image),
        "total_s": round(elapsed, 1),
        "story_s": _phase_seconds(state, "phase1"),
        "audio_s": _phase_seconds(state, "phase2"),
        "video_s": _phase_seconds(state, "phase3"),
    }


@contextmanager
def _watch_calls():
    """Record which provider served every image, voice line and LLM call in the block.

    Only provider, model and outcome are kept, never prompts, keys or error
    text, so a report is safe to share. Worker threads append at the same
    time, which is safe because list.append is atomic in CPython.
    """
    import agents.story_agent.agent as story_module
    from mcp.tool_executor import ToolExecutor
    from mcp.tools.llm_tools.llm_client import LLMClient

    calls: Dict[str, List[Any]] = {"images": [], "voices": [], "llm": [], "template_scripts": []}
    real_execute, real_call = ToolExecutor.execute, LLMClient._call
    real_template = story_module.template_script

    def execute(self, tool, **kwargs):
        result = real_execute(self, tool, **kwargs)
        meta = result.metadata or {}
        if tool == "vision.generate_image":
            calls["images"].append({"provider": meta.get("provider", "failed"),
                                    "model": meta.get("model"), "attempt": meta.get("attempt")})
        elif tool == "audio.tts":
            calls["voices"].append({"engine": meta.get("engine", "failed")})
        return result

    def call(self, spec, *args, **kwargs):
        # "ok" means the provider answered. The caller can still reject the
        # answer (bad JSON, empty text) and ask again or move down the chain.
        record = {"role": self.role, "provider": spec.provider, "model": spec.model, "ok": False}
        calls["llm"].append(record)
        try:
            text = real_call(self, spec, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            record["error"] = type(e).__name__
            raise
        record["ok"] = True
        return text

    def template(*args, **kwargs):
        # The story agent quietly writes its fixed template when no model could.
        # Such a run says nothing about script quality, so it has to show.
        calls["template_scripts"].append(True)
        return real_template(*args, **kwargs)

    ToolExecutor.execute, LLMClient._call = execute, call
    story_module.template_script = template
    try:
        yield calls
    finally:
        ToolExecutor.execute, LLMClient._call = real_execute, real_call
        story_module.template_script = real_template


def _script_source(calls: Dict[str, List[Any]]) -> str:
    """The provider whose script was used, or "template" if no model wrote one."""
    if calls["template_scripts"]:
        return "template"
    served = [c for c in calls["llm"] if c["role"] == "story" and c["ok"]]
    return served[-1]["provider"] if served else "unknown"


def run_one(prompt: str, args) -> Dict[str, Any]:
    from agents.orchestrator import PipelineOrchestrator

    with _watch_calls() as calls:
        started = time.monotonic()
        state = PipelineOrchestrator().run_full(
            prompt=prompt,
            target_duration_s=args.duration,
            scene_count=args.scenes,
            with_bgm=not args.no_bgm,
            with_subtitles=not args.no_subs,
            subtitle_language=args.subtitle_lang,
            width=args.width, height=args.height, fps=args.fps,
        )
        elapsed = time.monotonic() - started
    providers_used = Counter(image["provider"] for image in calls["images"])
    from shared import providers
    preferred = providers.active("image")
    row = measure(state, args.duration, elapsed, providers_used,
                  preferred_image=preferred.provider if preferred else "")
    row["prompt"] = prompt
    return row


# ---- the labelled test set (eval/testset.jsonl) ----------------------------

def load_testset(path, ids: Optional[List[str]] = None,
                 render_subset: bool = False) -> List[Dict[str, Any]]:
    """Read a labelled test set, one prompt per line, optionally narrowed down.

    Every line is checked before anything runs, so a typo fails at once rather
    than after the first render has spent its images.
    """
    from shared.languages import canonical

    cases: List[Dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path} line {number}"
        try:
            case = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{where}: not valid JSON ({e.msg})") from None
        missing = [field for field in TESTSET_FIELDS if field not in case]
        if missing:
            raise ValueError(f"{where}: missing {', '.join(missing)}")
        if not isinstance(case["prompt"], str) or not case["prompt"].strip():
            raise ValueError(f"{where}: the prompt is empty")
        for field in ("target_duration_s", "expected_scenes"):
            value = case[field]
            # bool is a subclass of int, so `true` would otherwise pass as 1.
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{where}: {field} must be a whole number above 0")
        language = case["subtitle_language"]
        if not isinstance(language, str) or canonical(language) != language:
            raise ValueError(f"{where}: subtitle_language {language!r} is not a name "
                             "from shared/languages.py")
        cases.append(case)

    seen = [case["id"] for case in cases]
    duplicates = sorted({i for i in seen if seen.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path}: duplicate id(s) {', '.join(duplicates)}")
    if ids:
        unknown = sorted(set(ids) - set(seen))
        if unknown:
            raise ValueError(f"{path}: no prompt with id(s) {', '.join(unknown)}")
        cases = [case for case in cases if case["id"] in ids]
    if render_subset:
        cases = [case for case in cases if case["render_subset"] is True]
    return cases


def run_case(case: Dict[str, Any], args) -> Dict[str, Any]:
    """Plan one test-set prompt, then render it if it is in the render subset.

    Plan-then-render is the creator interface's own path (run_full is only the
    CLI's), and the render reuses each storyboard preview as that scene's wide
    shot instead of drawing it again.
    """
    from agents.orchestrator import PipelineOrchestrator
    from shared import providers

    row: Dict[str, Any] = {field: case[field] for field in TESTSET_FIELDS}
    orchestrator = PipelineOrchestrator()

    with _watch_calls() as planned:
        started = time.monotonic()
        state = orchestrator.plan(case["prompt"], target_duration_s=case["target_duration_s"],
                                  scene_count=case["expected_scenes"])
        plan_s = time.monotonic() - started
    cast = [c for c in state.script.characters.characters if c.role != "narrator"]
    row.update({
        "project_id": state.project_id,
        "plan_s": round(plan_s, 1),
        "script_source": _script_source(planned),
        "scenes": len(state.script.scenes),
        "characters": len(cast),     # the narrator is not counted, as in the labels
        "character_names": [c.name for c in cast],
        "storyboard_images": planned["images"],
        "llm_calls": planned["llm"],
        "render": None,
    })
    if not case["render_subset"]:
        return row

    with _watch_calls() as rendered:
        started = time.monotonic()
        state = orchestrator.render(state.project_id, with_bgm=not args.no_bgm,
                                    with_subtitles=not args.no_subs,
                                    subtitle_language=case["subtitle_language"])
        render_s = time.monotonic() - started
    # The film is made of the previews (its wide shots) and the render's own images.
    film_images = Counter(i["provider"] for i in planned["images"] + rendered["images"])
    preferred = providers.active("image")
    render = measure(state, case["target_duration_s"], plan_s + render_s, film_images,
                     preferred_image=preferred.provider if preferred else "")
    render.update({"render_s": round(render_s, 1), "images": rendered["images"],
                   "voices": rendered["voices"], "llm_calls": rendered["llm"]})
    row["render"] = render
    return row


def _redact(text: str) -> str:
    """Blank out any credential from the environment that an error message quotes."""
    for name, value in os.environ.items():
        if len(value) >= 8 and any(word in name for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            text = text.replace(value, "***")
    return text


def _git_commit() -> str:
    """The commit measured, with "-dirty" if the working tree had changes."""
    repo = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                               capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return commit + ("-dirty" if dirty else "")


def _case_summary(row: Dict[str, Any]) -> str:
    if row.get("error"):
        return f"    FAILED: {row['error']}"
    line = (f"    plan {row['plan_s']}s · {row['scenes']}/{row['expected_scenes']} scenes · "
            f"script by {row['script_source']}")
    render = row["render"]
    if render:
        line += (f" · render {render['render_s']}s · film {render['film_s']}s "
                 f"(target {render['target_s']}s) · in sync: {render['in_sync']} · "
                 f"images {render['image_providers']}")
    return line


def run_testset(args) -> int:
    from shared import providers

    # An Urdu or emoji prompt must not crash a run whose output goes to a file,
    # where Windows encodes stdout as cp1252 and cannot write those characters.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ids =[i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None
    cases = load_testset(args.testset, ids=ids, render_subset=args.render_subset)
    report: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "command": "python " + " ".join(sys.argv),
        "commit": _git_commit(),
        "testset": str(args.testset),
        "image_provider": str(providers.active("image")),
        "llm_provider": str(providers.active("story")),
        "tts_provider": str(providers.active("tts")),
        "runs": [],
    }
    out_dir = ROOT / "data" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_testset.json"

    for case in cases:
        print(f"\n=== {case['id']}: {case['prompt'][:70]}", flush=True)
        try:
            row = run_case(case, args)
        except Exception as e:  # noqa: BLE001  (one broken film must not stop the batch)
            row = {field: case[field] for field in TESTSET_FIELDS}
            row["error"] = _redact(f"{type(e).__name__}: {e}")[:300]
        report["runs"].append(row)
        # Saved after every prompt, so a long run that is stopped keeps what it finished.
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(_case_summary(row), flush=True)

    print(f"\nsaved: {out}")
    broken = [r for r in report["runs"]
              if r.get("error") or (r.get("render") and not r["render"]["in_sync"])]
    return 1 if broken else 0


def to_markdown(report: Dict[str, Any]) -> str:
    rows = report["runs"]
    lines = [
        f"# Benchmark: {report['started_at']}",
        "",
        f"`{report['command']}`",
        "",
        f"- providers: image **{report['image_provider']}**, "
        f"llm **{report['llm_provider']}**, tts **{report['tts_provider']}**",
        f"- {len(rows)} prompt(s), target {report['target_s']}s, {report['scenes']} scenes",
        "",
        "| prompt | total | story | audio | video | film | vs target | in sync | lines on a cut | fallback images |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['prompt'][:38]} | {r['total_s']}s | {r['story_s']}s | {r['audio_s']}s | "
            f"{r['video_s']}s | {r['film_s']}s | {r['duration_error_pct']}% | "
            f"{'yes' if r['in_sync'] else 'NO'} | {r['lines_on_a_cut']}/{r['lines']} | "
            f"{r['fallback_images']} |"
        )
    totals = report["totals"]
    lines += [
        "",
        f"**Totals:** {totals['runs']} runs, {totals['total_s']}s, "
        f"all in sync: {'yes' if totals['all_in_sync'] else 'NO'}, "
        f"every line on a cut: {'yes' if totals['all_lines_on_cuts'] else 'NO'}, "
        f"mean length error {totals['mean_duration_error_pct']}%, "
        f"fallback images {totals['fallback_images']}.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="force mock LLM / placeholder images / silent voices")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--scenes", type=int, default=3)
    parser.add_argument("--prompts", type=int, default=len(PROMPTS))
    parser.add_argument("--subtitle-lang", default="English")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--no-bgm", action="store_true")
    parser.add_argument("--no-subs", action="store_true")
    parser.add_argument("--out", help="also write a markdown report here")
    parser.add_argument("--testset", help="read the prompts from a labelled test set such as "
                        "eval/testset.jsonl; each brings its own length, scene count and "
                        "subtitle language, so --duration, --scenes, --subtitle-lang, "
                        "--prompts, --width, --height and --fps are ignored")
    parser.add_argument("--ids", help="with --testset: only these ids, comma-separated")
    parser.add_argument("--render-subset", action="store_true",
                        help="with --testset: only the prompts marked render_subset")
    args = parser.parse_args()
    if (args.ids or args.render_subset) and not args.testset:
        parser.error("--ids and --render-subset need --testset")
    if args.testset and args.out:
        parser.error("--out is not used with --testset; the report is the JSON "
                     "written to data/benchmarks/")

    if args.offline:
        _offline()

    import mcp.tools  # noqa: F401  (registers the tools)
    from shared import providers

    if args.testset:
        return run_testset(args)

    report: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "command": "python " + " ".join(sys.argv[0:1] + sys.argv[1:]),
        "target_s": args.duration,
        "scenes": args.scenes,
        "image_provider": str(providers.active("image")),
        "llm_provider": str(providers.active("story")),
        "tts_provider": str(providers.active("tts")),
        "runs": [],
    }

    for prompt in PROMPTS[:max(1, args.prompts)]:
        print(f"\n=== {prompt}")
        row = run_one(prompt, args)
        report["runs"].append(row)
        print(f"    {row['total_s']}s total · film {row['film_s']}s "
              f"(target {row['target_s']}s) · in sync: {row['in_sync']} · "
              f"images {row['image_providers']}")

    runs: List[Dict[str, Any]] = report["runs"]
    report["totals"] = {
        "runs": len(runs),
        "total_s": round(sum(r["total_s"] for r in runs), 1),
        "all_in_sync": all(r["in_sync"] for r in runs),
        "all_lines_on_cuts": all(r["lines_on_a_cut"] == r["lines"] for r in runs),
        "mean_duration_error_pct": round(
            sum(r["duration_error_pct"] for r in runs) / len(runs), 1),
        "fallback_images": sum(r["fallback_images"] for r in runs),
    }

    out_dir = ROOT / "data" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out_dir / f"{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = to_markdown(report)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
    print()
    print(markdown)
    print(f"saved: data/benchmarks/{stamp}.json" + (f" and {args.out}" if args.out else ""))
    return 0 if report["totals"]["all_in_sync"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
