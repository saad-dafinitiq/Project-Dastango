"""The content store: every file a version keeps, once, under the hash of what is in it (DST-30).

A version used to copy every file of the film (`state_versions/<project>/v<n>/...`), so three small edits
took one film from 15.8 MB to 36.3 MB: the edit changes one voice and one scene's clips, and the other
thirty files were copied again. Now a version holds a manifest, `{path in the project: sha256, size}`,
and the files themselves live here, once each, however many versions (of however many films) name them.

`BlobStore` is the interface, `LocalBlobStore` the implementation on the data folder. An S3 or R2 store
is another implementation of the same five methods; nothing above this file knows where the bytes are.

A blob is written to a temporary name and renamed into place, so a reader never sees half of one and
two writers of the same content cannot disagree. Blobs are never changed after they are written; the
only way one goes away is `collect_garbage`, which keeps everything any version names.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

from shared import constants

CHUNK = 1 << 20
SETTLED_NS = 2_000_000_000
_hashed: Dict[Tuple[str, int, int], str] = {}


class BlobMissing(FileNotFoundError):
    """A version names content the store no longer has."""


def hash_file(path: Path) -> str:
    """The sha256 of a file. Remembered for the file's (path, size, mtime), so a snapshot of a film whose
    files did not change since the last one does not read them again.

    Only a file that has been still for a couple of seconds is remembered: file times tick every few
    milliseconds, so a small file rewritten to the same size within the tick of its last write has the same
    (size, mtime), and a remembered hash would then name the old content for the new. A file that old can only
    be changed later with a later time."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    known = _hashed.get(key)
    if known:
        return known
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    if time.time_ns() - st.st_mtime_ns > SETTLED_NS:
        if len(_hashed) > 20_000:
            _hashed.clear()
        _hashed[key] = digest.hexdigest()
    return digest.hexdigest()


class BlobStore:
    """Where content lives, by hash."""

    def has(self, sha: str) -> bool:
        raise NotImplementedError

    def put(self, path: Path, link: bool = False) -> Tuple[str, int]:
        """Keep the file's content; (sha256, size). `link`: the file will never change (a legacy
        snapshot copy being migrated), so the store may share its storage instead of copying it."""
        raise NotImplementedError

    def get(self, sha: str, dest: Path) -> None:
        """Write the content to `dest` (a new, independent file); BlobMissing if it is not here."""
        raise NotImplementedError

    def delete(self, sha: str) -> int:
        """Remove a blob; the bytes freed."""
        raise NotImplementedError

    def entries(self) -> Iterator[Tuple[str, int, float]]:
        """(sha, size, last written as epoch seconds) of every blob."""
        raise NotImplementedError


class LocalBlobStore(BlobStore):
    def __init__(self, root: Optional[Path] = None):
        self._root = root

    @property
    def root(self) -> Path:
        # Looked up when used, not when built, so a test (or a deployment) that moves STATE_DIR moves this too.
        return self._root or (constants.STATE_DIR / "_blobs")

    def _path(self, sha: str) -> Path:
        return self.root / sha[:2] / sha

    def has(self, sha: str) -> bool:
        return self._path(sha).is_file()

    def put(self, path: Path, link: bool = False) -> Tuple[str, int]:
        sha = hash_file(path)
        size = path.stat().st_size
        dest = self._path(sha)
        if dest.is_file():
            if dest.stat().st_size == size:
                os.utime(dest, None)             # written again just now: the garbage collector's clock restarts
                return sha, size
            dest.unlink()                        # the wrong size is a damaged copy: replace it
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{sha}.{uuid.uuid4().hex[:8]}.part")
        try:
            if link:
                try:
                    os.link(path, tmp)
                except OSError:
                    shutil.copyfile(path, tmp)
            else:
                shutil.copyfile(path, tmp)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        os.utime(dest, None)
        return sha, size

    def get(self, sha: str, dest: Path) -> None:
        src = self._path(sha)
        if not src.is_file():
            raise BlobMissing(f"the version store has no content {sha[:12]}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

    def delete(self, sha: str) -> int:
        p = self._path(sha)
        try:
            size = p.stat().st_size
            p.unlink()
        except FileNotFoundError:
            return 0
        try:
            p.parent.rmdir()                     # only if it is now empty
        except OSError:
            pass
        return size

    def entries(self) -> Iterator[Tuple[str, int, float]]:
        if not self.root.exists():
            return
        for sub in self.root.iterdir():
            if not sub.is_dir():
                continue
            for p in sub.iterdir():
                if p.is_file() and not p.name.startswith("."):
                    st = p.stat()
                    yield p.name, st.st_size, st.st_mtime


def store() -> BlobStore:
    """The content store this deployment uses: the data folder's."""
    return LocalBlobStore()


def now() -> float:
    return time.time()
