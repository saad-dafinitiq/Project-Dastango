"""Storage growth of one film across versions (DST-10 "before", DST-30 "after").

Renders one film with placeholder pictures (so the run is reproducible), applies three edits, and after
each step measures what the project takes on disk: its live files in `outputs/<project>` and everything
the version store holds (`state_versions/`, which since DST-30 also holds the shared content store).
Run it against a throwaway data folder so nothing else is counted:

    DATA_DIR=/tmp/growth python scripts/storage_growth.py --label after

It uses the offline script writer (`LLM_PROVIDER=mock`) and Kokoro voices when the model is present; the
numbers therefore differ from the DST-10 run, which used a model-written script, and the point is the
ratio between "before" (a copy per version) and "after" (one copy per content), measured the same way.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EDITS = ["make the voices in scene 2 whispered", "make scene 1 darker", "make everyone louder"]
PROMPT = "A night-shift nurse hears a call bell ringing from a ward that closed years ago"     # dst-03


def size(path: Path) -> dict:
    files = [p for p in path.rglob("*") if p.is_file()] if path.exists() else []
    return {"bytes": sum(p.stat().st_size for p in files), "files": len(files)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if not os.environ.get("DATA_DIR"):
        sys.exit("set DATA_DIR to a throwaway folder first: it must hold nothing else")
    os.environ.setdefault("PIPELINE_SKIP_DOTENV", "1")
    os.environ["PROVIDER_IMAGE"] = "placeholder"
    os.environ.setdefault("LLM_PROVIDER", "mock")
    model = ROOT / "data" / "models" / "kokoro"
    if (model / "kokoro-v1.0.onnx").exists():
        os.environ["KOKORO_MODEL"] = str(model / "kokoro-v1.0.onnx")
        os.environ["KOKORO_VOICES"] = str(model / "voices-v1.0.bin")

    import mcp.tools  # noqa: F401
    from agents.orchestrator import PipelineOrchestrator
    from shared import constants

    def measure(pid: str, step: str, seconds: float) -> dict:
        live = size(Path(constants.OUTPUTS_DIR) / pid)
        kept = size(Path(constants.STATE_DIR))
        row = {"step": step, "outputs": live, "versions": kept, "seconds": round(seconds, 1),
               "total_bytes": live["bytes"] + kept["bytes"]}
        print(f"{step:46} outputs {live['bytes'] / 1e6:7.2f} MB ({live['files']:3} files) | "
              f"versions {kept['bytes'] / 1e6:7.2f} MB ({kept['files']:3} files) | "
              f"total {row['total_bytes'] / 1e6:7.2f} MB", flush=True)
        return row

    orch = PipelineOrchestrator()
    steps = []
    t = time.monotonic()
    state = orch.plan(PROMPT, target_duration_s=20, scene_count=2)
    pid = state.project_id
    steps.append(measure(pid, "after plan (storyboard)", time.monotonic() - t))
    t = time.monotonic()
    orch.render(pid, subtitle_language="English")
    steps.append(measure(pid, "after render", time.monotonic() - t))
    for n, query in enumerate(EDITS, start=1):
        t = time.monotonic()
        result = orch.edit(pid, query)
        steps.append(measure(pid, f"after edit {n}: {query}", time.monotonic() - t))
        if not result.success:
            print("  edit failed:", result.error)
    report = {"label": args.label, "project_id": pid, "prompt": PROMPT, "steps": steps}
    out = Path(args.out) if args.out else Path(os.environ["DATA_DIR"]) / f"storage_growth_{args.label}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("saved:", out)


if __name__ == "__main__":
    main()
