"""Which pictures are stand-ins (DST-38).

When every image provider is at its limit the render still finishes, with a
placeholder drawn locally in place of the picture. The designed behaviour is to
carry on, but the creator must be told, and the state schema has no field for
it, so a small marker file sits beside the picture. The marker holds the
picture's size when it was written; a picture drawn properly afterwards has
another size, so a stale marker is never believed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional


def _marker(path: str | Path) -> Path:
    p = Path(path)
    return p.with_name(p.name + ".standin")


def mark(path: str | Path, stand_in: bool) -> None:
    """Record that `path` is (or is no longer) a stand-in picture."""
    m = _marker(path)
    try:
        if stand_in and Path(path).exists():
            m.write_text(str(Path(path).stat().st_size), encoding="utf-8")
        else:
            m.unlink(missing_ok=True)
    except OSError:
        pass


def is_stand_in(path: Optional[str | Path]) -> bool:
    if not path:
        return False
    m = _marker(path)
    try:
        return m.exists() and m.read_text(encoding="utf-8").strip() == str(Path(path).stat().st_size)
    except OSError:
        return False


def scenes_with_stand_ins(frames: Iterable) -> List[str]:
    """Ids of the scenes whose pictures include a stand-in, in film order."""
    out: List[str] = []
    for f in frames:
        bank = list(getattr(f, "shot_bank", None) or []) or [f.image_path]
        if any(is_stand_in(p) for p in bank):
            out.append(f.scene_id)
    return out
