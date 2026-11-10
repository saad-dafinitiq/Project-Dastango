"""Disk-level snapshot/restore of pipeline assets.

Since DST-30 a version does not copy the film's files: it records, in `v<n>/manifest.json`, which
content each file of the project held (`{path: {sha256, size}}`), and the content is kept once, by hash,
in the store (`state_manager/blobs.py`). A version made before then has its files copied under
`v<n>/` instead (a "legacy" version): it is read as it always was, and `migrate_version` moves it over.
"""
from __future__ import annotations
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from shared import constants
from shared.utils.files import ensure_dir, project_dir

from . import blobs

MANIFEST = "manifest.json"
STATE_FILE = "state.json"


def referenced_files(state: Any, extra: Iterable[str] = ()) -> List[str]:
    """Every existing file inside the project directory that `state` points to.

    Walks the whole serialized state (script, audio, video, phase artifact
    lists), so any file an agent or edit records is captured by snapshots
    without maintaining separate asset lists.
    """
    proj_root = project_dir(state.project_id).resolve()
    found: Dict[str, None] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                visit(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                visit(v)
        elif isinstance(value, str) and ("/" in value or "\\" in value) and len(value) < 1024:
            p = Path(value)
            try:
                if p.is_file() and p.resolve().is_relative_to(proj_root):
                    found.setdefault(str(p), None)
            except (OSError, ValueError):
                pass

    visit(state.model_dump(mode="json"))
    for path in extra:
        if path and Path(path).is_file():
            found.setdefault(str(path), None)
    return list(found)


def _relative(src: Path, proj_root: Path) -> Path:
    try:
        return src.relative_to(proj_root)
    except ValueError:
        return Path(src.name)


def _files_of(paths: Iterable[str], proj_root: Path) -> Dict[str, Path]:
    """The files named by `paths` (a directory means every file under it), by their path in the project."""
    found: Dict[str, Path] = {}
    for src in paths:
        p = Path(src)
        if not p.exists():
            continue
        for f in ([q for q in p.rglob("*") if q.is_file()] if p.is_dir() else [p]):
            found[_relative(f, proj_root).as_posix()] = f
    return found


def _write_manifest(snap_root: Path, files: Dict[str, Dict[str, Any]]) -> None:
    ensure_dir(snap_root)
    tmp = snap_root / f".{MANIFEST}.part"
    tmp.write_text(json.dumps({"format": 1, "files": files}, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, snap_root / MANIFEST)


def read_manifest(snap_root: Path) -> Optional[Dict[str, Dict[str, Any]]]:
    """`{path in the project: {sha256, size}}` of a version, or None for a legacy version (files copied)."""
    path = snap_root / MANIFEST
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("files", {})


def snapshot_assets(project_id: str, version: int, asset_paths: List[str]) -> List[str]:
    """Keep each asset in the content store and record it in the version's manifest.

    Returns the path each file has in the version (`v<n>/<path>`): the names the history shows. They are
    no longer files on disk; the manifest and the store are the record."""
    snap_root = ensure_dir(constants.STATE_DIR / project_id / f"v{version}")
    proj_root = project_dir(project_id)
    store = blobs.store()
    files: Dict[str, Dict[str, Any]] = {}
    for rel, path in _files_of(asset_paths, proj_root).items():
        mtime = path.stat().st_mtime_ns
        sha, size = store.put(path)
        # The file's time is part of the record: a scene's cache signature reads its pictures' times, and
        # a restore that gave every file a new one would draw the whole film again after every edit.
        files[rel] = {"sha256": sha, "size": size, "mtime_ns": mtime}
    _write_manifest(snap_root, files)
    return [str(snap_root / rel) for rel in sorted(files)]


def restore_assets(project_id: str, version: int) -> List[str]:
    """Write a version's files back into the live project directory."""
    snap_root = constants.STATE_DIR / project_id / f"v{version}"
    if not snap_root.exists():
        raise FileNotFoundError(f"snapshot v{version} not found for {project_id}")
    proj_root = ensure_dir(project_dir(project_id))
    store = blobs.store()
    restored: List[str] = []
    manifest = read_manifest(snap_root) or {}
    for rel, meta in manifest.items():
        dst = proj_root / rel
        mtime = meta.get("mtime_ns")
        if mtime is not None and dst.is_file():
            st = dst.stat()
            if st.st_size == meta["size"] and st.st_mtime_ns == mtime:
                restored.append(str(dst))          # already exactly this file: an edit would have changed its time
                continue
        store.get(meta["sha256"], dst)             # a new file, never a link: an edit changes live files in place
        if mtime is not None:
            os.utime(dst, ns=(mtime, mtime))       # copy2 did this: the file is as old as it was
        restored.append(str(dst))
    for src in snap_root.rglob("*"):
        # Files copied under the version, from before DST-30 (or a migration that stopped half way). The
        # version's own record is not a project file: copying it in made a revert snapshot it straight
        # back over the new version's record, so every revert was saved claiming to be the version it
        # went back to.
        if src.is_dir() or src.parent == snap_root and src.name in (STATE_FILE, MANIFEST) or src.name.startswith("."):
            continue
        rel = src.relative_to(snap_root)
        if rel.as_posix() in manifest:
            continue
        dst = proj_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(str(dst))
    return restored


# ---- legacy versions, and what the store holds -------------------------------------------------------

def version_dirs(project_id: Optional[str] = None) -> List[Tuple[str, int, Path]]:
    """(project, version, directory) of every version on disk, for one project or all."""
    root = constants.STATE_DIR
    out: List[Tuple[str, int, Path]] = []
    if not root.exists():
        return out
    for proj in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if project_id and proj.name != project_id:
            continue
        for v in proj.iterdir():
            if v.is_dir() and v.name[:1] == "v" and v.name[1:].isdigit():
                out.append((proj.name, int(v.name[1:]), v))
    return sorted(out, key=lambda x: (x[0], x[1]))


def _legacy_files(snap_root: Path) -> List[Path]:
    return [p for p in snap_root.rglob("*")
            if p.is_file() and not p.name.startswith(".")
            and not (p.parent == snap_root and p.name in (STATE_FILE, MANIFEST))]


def migrate_version(project_id: str, version: int) -> Dict[str, int]:
    """Move a legacy version's copied files into the content store; {files, bytes_freed}. Safe to stop at
    any point and run again: the manifest is written after every file is in the store, and the copies are
    removed only after the manifest is."""
    snap_root = constants.STATE_DIR / project_id / f"v{version}"
    legacy = _legacy_files(snap_root)
    if not legacy:
        return {"files": 0, "bytes_freed": 0}
    store = blobs.store()
    files = dict(read_manifest(snap_root) or {})
    for src in legacy:
        mtime = src.stat().st_mtime_ns               # the copy kept the original's time
        sha, size = store.put(src, link=True)        # a copy nobody changes: sharing its storage is safe
        files[src.relative_to(snap_root).as_posix()] = {"sha256": sha, "size": size, "mtime_ns": mtime}
    _write_manifest(snap_root, files)
    freed = 0
    for src in legacy:
        freed += src.stat().st_size
        src.unlink()
    for d in sorted((p for p in snap_root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            d.rmdir()
        except OSError:
            pass
    return {"files": len(legacy), "bytes_freed": freed}


def migrate_all(project_id: Optional[str] = None) -> Dict[str, int]:
    done = {"versions": 0, "files": 0, "bytes_freed": 0}
    for pid, ver, _dir in version_dirs(project_id):
        moved = migrate_version(pid, ver)
        if moved["files"]:
            done["versions"] += 1
            done["files"] += moved["files"]
            done["bytes_freed"] += moved["bytes_freed"]
    return done


def referenced_hashes() -> Dict[str, int]:
    """Every content any version names: sha256 -> how many versions name it."""
    counts: Dict[str, int] = {}
    for _pid, _ver, d in version_dirs():
        for meta in (read_manifest(d) or {}).values():
            counts[meta["sha256"]] = counts.get(meta["sha256"], 0) + 1
    return counts


def collect_garbage(dry_run: bool = False, grace_s: float = 3600.0) -> Dict[str, int]:
    """Delete the content no version names. A blob written within `grace_s` is kept whatever names it: a
    snapshot writes its content first and its manifest after, and must not lose the first to a sweep that
    runs between the two. Nothing a manifest names is ever deleted."""
    keep = referenced_hashes()
    store = blobs.store()
    cutoff = time.time() - grace_s
    removed = freed = kept_young = 0
    for sha, size, written in list(store.entries()):
        if sha in keep:
            continue
        if written > cutoff:
            kept_young += 1
            continue
        removed += 1
        freed += size if dry_run else store.delete(sha)
    return {"unreferenced": removed, "bytes_freed": freed, "kept_recent": kept_young, "dry_run": int(dry_run)}


def stats() -> Dict[str, int]:
    """What the versions name against what the store holds (the saving), and what is still copied."""
    held = {sha: size for sha, size, _t in blobs.store().entries()}
    named = versions = legacy_versions = legacy_bytes = 0
    for _pid, _ver, d in version_dirs():
        versions += 1
        manifest = read_manifest(d)
        if manifest:
            named += sum(m["size"] for m in manifest.values())
        old = _legacy_files(d)
        if old:
            legacy_versions += 1
            legacy_bytes += sum(p.stat().st_size for p in old)
    return {"versions": versions, "blobs": len(held), "stored_bytes": sum(held.values()),
            "named_bytes": named, "legacy_versions": legacy_versions, "legacy_bytes": legacy_bytes}
