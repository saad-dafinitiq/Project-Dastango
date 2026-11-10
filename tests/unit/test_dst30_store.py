"""DST-30: each file a film's versions keep is stored once, by the hash of its content.

A version used to copy every file of the film, so three small edits took one film from 15.8 MB to 36.3 MB
(DST-10) and, measured again on the same kind of film, from 47.8 MB to 104.8 MB. Now a version is a manifest
and the content lives once in a store. These tests hold the promises: identical content is stored once,
a restore gives back exactly the bytes, a version made the old way still restores and can be moved over, and
the sweep that clears unreferenced content never touches what a version names.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

import shared.constants as constants
from shared.schemas.pipeline import PipelineState
from state_manager import blobs, snapshot
from state_manager.state_manager import StateManager
from state_manager.storage import VersionStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(constants, "OUTPUTS_DIR", tmp_path / "out")
    monkeypatch.setattr(constants, "DB_PATH", tmp_path / "db.sqlite")
    constants.STATE_DIR.mkdir(parents=True)
    constants.OUTPUTS_DIR.mkdir(parents=True)
    proj = constants.OUTPUTS_DIR / "p"
    (proj / "video").mkdir(parents=True)
    sm = StateManager(VersionStore(tmp_path / "db.sqlite"))
    return proj, sm, PipelineState(project_id="p", user_prompt="x")


def _put(proj: Path, name: str, data: bytes) -> Path:
    p = proj / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _blob_files() -> list[Path]:
    root = constants.STATE_DIR / "_blobs"
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_identical_files_are_stored_once_however_many_versions_name_them(env):
    proj, sm, state = env
    film = _put(proj, "video/film.mp4", b"F" * 100_000)
    voice = _put(proj, "audio/line_1.wav", b"V" * 50_000)
    for n in range(4):
        sm.snapshot(state, asset_paths=[str(film), str(voice)], description=f"v{n}")
    assert len(_blob_files()) == 2                          # two contents, four versions
    assert sum(p.stat().st_size for p in _blob_files()) == 150_000
    # every version still names both
    for n in (1, 2, 3, 4):
        manifest = snapshot.read_manifest(constants.STATE_DIR / "p" / f"v{n}")
        assert set(manifest) == {"video/film.mp4", "audio/line_1.wav"}


def test_a_changed_file_adds_only_its_own_content(env):
    proj, sm, state = env
    film = _put(proj, "video/film.mp4", b"F" * 100_000)
    voice = _put(proj, "audio/line_1.wav", b"V" * 50_000)
    sm.snapshot(state, asset_paths=[str(film), str(voice)], description="v1")
    voice.write_bytes(b"W" * 50_000)
    sm.snapshot(state, asset_paths=[str(film), str(voice)], description="v2")
    assert len(_blob_files()) == 3
    assert sum(p.stat().st_size for p in _blob_files()) == 200_000      # not 300_000: the film is held once


def test_a_version_of_unchanged_files_costs_almost_nothing(env):
    proj, sm, state = env
    files = [str(_put(proj, f"video/f{i}.bin", os.urandom(40_000))) for i in range(10)]
    sm.snapshot(state, asset_paths=files, description="v1")
    first = sum(p.stat().st_size for p in constants.STATE_DIR.rglob("*") if p.is_file())
    for n in range(5):
        sm.snapshot(state, asset_paths=files, description="again")
    total = sum(p.stat().st_size for p in constants.STATE_DIR.rglob("*") if p.is_file())
    assert total < first * 1.25, (first, total)             # six versions, not six copies


def test_a_restore_gives_back_exactly_the_bytes_of_that_version(env):
    proj, sm, state = env
    film = _put(proj, "video/film.mp4", b"first cut " * 1000)
    sm.snapshot(state, asset_paths=[str(film)], description="v1")
    film.write_bytes(b"second cut " * 1000)
    sm.snapshot(state, asset_paths=[str(film)], description="v2")
    sm.revert("p", 1)
    assert film.read_bytes() == b"first cut " * 1000
    sm.revert("p", 2)
    assert film.read_bytes() == b"second cut " * 1000


def test_changing_a_restored_file_never_changes_the_stored_content(env):
    """An edit rewrites the film's files in place (ffmpeg writes over them): a restored file must be a copy,
    not a link to the store, or the edit would change every version that names that content."""
    proj, sm, state = env
    film = _put(proj, "video/film.mp4", b"original" * 1000)
    sm.snapshot(state, asset_paths=[str(film)], description="v1")
    snapshot.restore_assets("p", 1)
    with film.open("r+b") as f:
        f.write(b"EDITED")
    assert film.read_bytes().startswith(b"EDITED")
    snapshot.restore_assets("p", 1)
    assert film.read_bytes() == b"original" * 1000
    assert all(_sha(p.read_bytes()) == p.name for p in _blob_files())       # the store is what it says it is


def test_a_file_changed_after_a_snapshot_does_not_change_that_snapshot(env):
    proj, sm, state = env
    film = _put(proj, "video/film.mp4", b"A" * 5000)
    sm.snapshot(state, asset_paths=[str(film)], description="v1")
    film.write_bytes(b"B" * 5000)                           # same size, straight after: the case a cached hash gets wrong
    sm.snapshot(state, asset_paths=[str(film)], description="v2")
    m1 = snapshot.read_manifest(constants.STATE_DIR / "p" / "v1")["video/film.mp4"]["sha256"]
    m2 = snapshot.read_manifest(constants.STATE_DIR / "p" / "v2")["video/film.mp4"]["sha256"]
    assert m1 == _sha(b"A" * 5000) and m2 == _sha(b"B" * 5000)


def test_a_missing_blob_is_said_not_papered_over(env):
    proj, sm, state = env
    film = _put(proj, "video/film.mp4", b"x" * 100)
    sm.snapshot(state, asset_paths=[str(film)], description="v1")
    _blob_files()[0].unlink()
    film.unlink()                                           # (a live file already exactly as saved needs no blob)
    with pytest.raises(blobs.BlobMissing):
        snapshot.restore_assets("p", 1)


def test_the_history_still_counts_and_names_a_versions_files(env):
    proj, sm, state = env
    film = _put(proj, "video/film.mp4", b"x" * 100)
    version = sm.snapshot(state, asset_paths=[str(film)], description="v1")
    assert [Path(p).name for p in version.asset_paths] == ["film.mp4"]
    assert sm.history("p")[0]["asset_paths"] == version.asset_paths


def test_a_directory_is_kept_file_by_file(env):
    proj, sm, state = env
    _put(proj, "frames/a.png", b"a" * 10)
    _put(proj, "frames/deep/b.png", b"b" * 10)
    sm.snapshot(state, asset_paths=[str(proj / "frames")], description="v1")
    assert set(snapshot.read_manifest(constants.STATE_DIR / "p" / "v1")) == {"frames/a.png", "frames/deep/b.png"}


# ---- versions made the old way -----------------------------------------------------------------------

def _legacy_version(version: int, files: dict) -> Path:
    """A version as it was stored before DST-30: its files copied under v<n>/, and its record."""
    d = constants.STATE_DIR / "p" / f"v{version}"
    for rel, data in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(data)
    (d / "state.json").write_text(json.dumps(PipelineState(project_id="p", user_prompt="x").model_dump(mode="json")),
                                  encoding="utf-8")
    return d


def test_a_version_made_the_old_way_still_restores(env):
    proj, _sm, _state = env
    _legacy_version(1, {"video/film.mp4": b"old film", "audio/a.wav": b"old voice"})
    restored = snapshot.restore_assets("p", 1)
    assert (proj / "video/film.mp4").read_bytes() == b"old film" and len(restored) == 2
    assert not (proj / "state.json").exists()               # the version's own record is not a project file


def test_migrating_moves_the_copies_into_the_store_and_the_version_still_restores(env):
    proj, _sm, _state = env
    _legacy_version(1, {"video/film.mp4": b"same film" * 500, "audio/a.wav": b"voice 1"})
    _legacy_version(2, {"video/film.mp4": b"same film" * 500, "audio/a.wav": b"voice 2"})
    before = snapshot.stats()
    assert before["legacy_versions"] == 2 and before["blobs"] == 0
    done = snapshot.migrate_all()
    assert done["versions"] == 2 and done["files"] == 4
    after = snapshot.stats()
    assert after["legacy_versions"] == 0 and after["blobs"] == 3        # the film once, two voices
    assert after["stored_bytes"] < before["legacy_bytes"]
    assert not (constants.STATE_DIR / "p" / "v1" / "video").exists()   # the copies are gone
    snapshot.restore_assets("p", 1)
    assert (proj / "audio/a.wav").read_bytes() == b"voice 1"
    snapshot.restore_assets("p", 2)
    assert (proj / "audio/a.wav").read_bytes() == b"voice 2"
    assert (proj / "video/film.mp4").read_bytes() == b"same film" * 500


def test_migrating_twice_is_a_no_op(env):
    _legacy_version(1, {"a.bin": b"abc" * 100})
    snapshot.migrate_all()
    assert snapshot.migrate_all() == {"versions": 0, "files": 0, "bytes_freed": 0}


def test_a_migration_that_stopped_half_way_loses_nothing_and_finishes(env):
    proj, _sm, _state = env
    d = _legacy_version(1, {"a.bin": b"aaa" * 100, "b.bin": b"bbb" * 100})
    # as if it stopped after the manifest was written and before the copies were removed
    store = blobs.store()
    files = {rel: dict(zip(("sha256", "size"), store.put(d / rel, link=True)), mtime_ns=(d / rel).stat().st_mtime_ns)
             for rel in ("a.bin", "b.bin")}
    snapshot._write_manifest(d, files)
    snapshot.restore_assets("p", 1)
    assert (proj / "b.bin").read_bytes() == b"bbb" * 100
    snapshot.migrate_all()
    assert not (d / "a.bin").exists()
    snapshot.restore_assets("p", 1)
    assert (proj / "a.bin").read_bytes() == b"aaa" * 100


def test_a_nested_state_json_is_a_project_file_not_a_record(env):
    proj, _sm, _state = env
    _legacy_version(1, {"video/state.json": b"{}"})
    snapshot.restore_assets("p", 1)
    assert (proj / "video/state.json").exists()


# ---- clearing what nothing names ---------------------------------------------------------------------

def _old(blob: Path, days: float = 2) -> None:
    t = time.time() - days * 86400
    os.utime(blob, (t, t))


def test_the_sweep_removes_unreferenced_content_and_never_referenced_content(env):
    proj, sm, state = env
    film = _put(proj, "video/film.mp4", b"kept" * 100)
    sm.snapshot(state, asset_paths=[str(film)], description="v1")
    orphan_sha, _ = blobs.store().put(_put(proj, "orphan.bin", b"nobody names this"))
    for p in _blob_files():
        _old(p)
    done = snapshot.collect_garbage()
    assert done["unreferenced"] == 1 and done["bytes_freed"] == len(b"nobody names this")
    assert [p.name for p in _blob_files()] == [_sha(b"kept" * 100)]
    snapshot.restore_assets("p", 1)                          # and the version it belongs to still works
    assert film.read_bytes() == b"kept" * 100
    assert not blobs.store().has(orphan_sha)


def test_the_sweep_keeps_what_was_written_a_moment_ago(env):
    """A snapshot writes its content first and its manifest after; a sweep between the two must not take it."""
    proj, _sm, _state = env
    blobs.store().put(_put(proj, "fresh.bin", b"just written"))
    assert snapshot.collect_garbage()["kept_recent"] == 1 and len(_blob_files()) == 1


def test_a_dry_run_removes_nothing(env):
    proj, _sm, _state = env
    blobs.store().put(_put(proj, "orphan.bin", b"x" * 10))
    for p in _blob_files():
        _old(p)
    assert snapshot.collect_garbage(dry_run=True)["unreferenced"] == 1 and len(_blob_files()) == 1


def test_content_any_one_version_names_survives_when_the_others_are_gone(env):
    proj, sm, state = env
    film = _put(proj, "video/film.mp4", b"shared" * 100)
    sm.snapshot(state, asset_paths=[str(film)], description="v1")
    sm.snapshot(state, asset_paths=[str(film)], description="v2")
    import shutil
    shutil.rmtree(constants.STATE_DIR / "p" / "v1")           # a version is deleted
    for p in _blob_files():
        _old(p)
    assert snapshot.collect_garbage()["unreferenced"] == 0    # v2 still names it
    snapshot.restore_assets("p", 2)
    assert film.read_bytes() == b"shared" * 100
    shutil.rmtree(constants.STATE_DIR / "p" / "v2")
    assert snapshot.collect_garbage()["unreferenced"] == 1    # now nothing does


def test_the_sweep_does_not_mistake_the_store_for_a_project(env):
    proj, sm, state = env
    sm.snapshot(state, asset_paths=[str(_put(proj, "a.bin", b"a"))], description="v1")
    assert [pid for pid, _v, _d in snapshot.version_dirs()] == ["p"]


# ---- the command --------------------------------------------------------------------------------------

def test_the_storage_command_reports_migrates_and_sweeps(env, capsys):
    import argparse

    import main

    proj, sm, state = env
    _legacy_version(1, {"a.bin": b"a" * 2000})
    ns = lambda action, **kw: argparse.Namespace(action=action, project_id=None, dry_run=False, grace_minutes=60.0, **kw)  # noqa: E731
    main.cmd_storage(ns("stats"))
    assert "still hold their own copies" in capsys.readouterr().out
    main.cmd_storage(ns("migrate"))
    assert "moved 1 files of 1 versions" in capsys.readouterr().out
    main.cmd_storage(ns("stats"))
    assert "still hold" not in capsys.readouterr().out
    main.cmd_storage(ns("gc"))
    assert "removed 0 files" in capsys.readouterr().out


# ---- a real film --------------------------------------------------------------------------------------

def test_three_edits_of_a_real_film_do_not_triple_what_it_takes(small_project):
    from agents.edit_agent import EditAgent
    from shared.schemas.edit import EditCommand
    from tests.conftest import silence_tts

    state, sm = small_project(project_id="t_d30")
    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)

    def kept() -> int:
        return sum(p.stat().st_size for p in constants.STATE_DIR.rglob("*") if p.is_file())

    first = kept()
    for query in ("apply the noir filter", "make scene 1 darker", "make everyone louder"):
        assert agent.edit(EditCommand(project_id="t_d30", query=query)).success
    assert kept() < first * 2.5, (first, kept())              # copying every file per version was 4x and more
    # and going back to the first cut still gives the first cut
    assert len(sm.history("t_d30")) >= 4


# ---- what an edit relies on ---------------------------------------------------------------------------

def test_a_restored_file_keeps_the_time_it_had(env):
    """A scene's cache signature reads its pictures' file times: a restore that gave every file a new time
    made every edit draw the whole film again (found by test_m1_edits)."""
    proj, sm, state = env
    pic = _put(proj, "video/scene_1.png", b"picture" * 100)
    then = time.time_ns() - 5_000_000_000
    os.utime(pic, ns=(then, then))
    sm.snapshot(state, asset_paths=[str(pic)], description="v1")
    pic.unlink()
    snapshot.restore_assets("p", 1)
    assert pic.stat().st_mtime_ns == then and pic.read_bytes() == b"picture" * 100


def test_a_migrated_file_keeps_its_time_too(env):
    proj, _sm, _state = env
    d = _legacy_version(1, {"video/scene_1.png": b"pic" * 50})
    then = time.time_ns() - 7_000_000_000
    os.utime(d / "video/scene_1.png", ns=(then, then))
    snapshot.migrate_all()
    snapshot.restore_assets("p", 1)
    assert (proj / "video/scene_1.png").stat().st_mtime_ns == then


def test_restoring_over_the_same_files_rewrites_nothing(env):
    """Every edit begins by putting the saved version's files back: files already as saved are left alone."""
    proj, sm, state = env
    pic = _put(proj, "video/scene_1.png", b"picture" * 100)
    old = time.time_ns() - 5_000_000_000
    os.utime(pic, ns=(old, old))
    sm.snapshot(state, asset_paths=[str(pic)], description="v1")
    inode = pic.stat().st_ino
    snapshot.restore_assets("p", 1)
    assert pic.stat().st_ino == inode and pic.stat().st_mtime_ns == old


def test_a_file_an_edit_changed_in_place_is_put_back(env):
    proj, sm, state = env
    pic = _put(proj, "video/scene_1.png", b"picture" * 100)
    old = time.time_ns() - 5_000_000_000
    os.utime(pic, ns=(old, old))
    sm.snapshot(state, asset_paths=[str(pic)], description="v1")
    pic.write_bytes(b"PICTURE" * 100)                       # same size, new time
    snapshot.restore_assets("p", 1)
    assert pic.read_bytes() == b"picture" * 100 and pic.stat().st_mtime_ns == old
