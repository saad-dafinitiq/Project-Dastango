"""M9 — backups leave the machine, come back intact, and only what changed goes up."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

ROOT = Path(__file__).resolve().parents[2]
BUCKET = "storygen-backups"


@pytest.fixture
def offsite(monkeypatch):
    spec = importlib.util.spec_from_file_location("offsite_backup",
                                                  ROOT / "scripts" / "offsite_backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, value in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
                        "S3_REGION": "us-east-1", "BACKUP_BUCKET": BUCKET}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield module


def _backup(root: Path, stamp: str, files: dict, dump: bytes = b"pg dump") -> Path:
    (root / "db").mkdir(parents=True, exist_ok=True)
    (root / "db" / f"{stamp}.dump").write_bytes(dump + stamp.encode())
    for rel, body in files.items():
        path = root / "data" / stamp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return root


def _tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


FILMS = {"outputs/p1/final_output.mp4": b"film" * 1000,
         "outputs/p1/video/frames/scene_1_wide.png": b"png" * 300,
         "state_versions/p1/v1/state.json": b'{"version": 1}'}


def test_a_backup_pushed_off_the_machine_comes_back_identical(offsite, tmp_path):
    here = _backup(tmp_path / "vm", "20261003T031500Z", FILMS)
    offsite.push(here, BUCKET, "backups", keep=14)

    elsewhere = tmp_path / "new-vm"
    stamp = offsite.pull(elsewhere, BUCKET, "backups")
    assert stamp == "20261003T031500Z"
    assert _tree(elsewhere / "data" / stamp) == _tree(here / "data" / stamp)
    assert (elsewhere / "db" / f"{stamp}.dump").read_bytes() == \
        (here / "db" / f"{stamp}.dump").read_bytes()


def test_the_next_night_uploads_only_what_changed(offsite, tmp_path):
    vm = _backup(tmp_path / "vm", "20261003T031500Z", FILMS)
    offsite.push(vm, BUCKET, "backups", keep=14)

    tonight = dict(FILMS)
    tonight["outputs/p1/final_output.mp4"] = b"edited film" * 1000        # changed
    tonight["state_versions/p1/v2/state.json"] = b'{"version": 2}'          # new
    del tonight["outputs/p1/video/frames/scene_1_wide.png"]                 # gone
    _backup(vm, "20261004T031500Z", tonight)
    stats = offsite.push(vm, BUCKET, "backups", keep=14)

    # Two films files and tonight's dump; last night's dump was already there.
    assert stats["uploaded"] == 3 and stats["unchanged"] == 1 and stats["deleted"] == 1
    back = offsite.pull(tmp_path / "check", BUCKET, "backups")
    assert back == "20261004T031500Z"
    assert _tree(tmp_path / "check" / "data" / back) == _tree(vm / "data" / back)


def test_only_the_newest_dumps_are_kept_up_there(offsite, tmp_path):
    vm = tmp_path / "vm"
    for day in range(1, 6):
        _backup(vm, f"2026100{day}T031500Z", FILMS)
    offsite.push(vm, BUCKET, "backups", keep=3)
    keys = sorted(o["Key"] for o in boto3.client("s3", region_name="us-east-1")
                  .list_objects_v2(Bucket=BUCKET, Prefix="backups/db/")["Contents"])
    assert keys == [f"backups/db/2026100{d}T031500Z.dump" for d in (3, 4, 5)]


def test_a_push_cut_short_leaves_the_last_whole_backup_named(offsite, tmp_path, monkeypatch):
    vm = _backup(tmp_path / "vm", "20261003T031500Z", FILMS)
    offsite.push(vm, BUCKET, "backups", keep=14)
    _backup(vm, "20261004T031500Z", {**FILMS, "outputs/p2/new.mp4": b"new"})

    real_client = offsite._client

    def failing_client():
        s3 = real_client()
        original = s3.upload_file

        def upload(path, bucket, key, **kw):
            if key.endswith("new.mp4"):
                raise ConnectionError("network dropped")
            return original(path, bucket, key, **kw)
        s3.upload_file = upload
        return s3
    monkeypatch.setattr(offsite, "_client", failing_client)
    with pytest.raises(ConnectionError):
        offsite.push(vm, BUCKET, "backups", keep=14)

    latest = boto3.client("s3", region_name="us-east-1").get_object(
        Bucket=BUCKET, Key="backups/LATEST")["Body"].read().decode()
    assert latest == "20261003T031500Z"


def test_content_shared_by_many_versions_goes_up_once_and_a_new_version_uploads_only_what_it_added(offsite, tmp_path):
    """DST-30: the version store keeps each file once, by hash, so the mirror holds it once too, and the
    night a new version is made only its manifest and its changed content go up."""
    film = "ab/" + "ab" * 32                       # content addressed: the same file for every version
    voice = "cd/" + "cd" * 32
    night1 = {f"state_versions/_blobs/{film}": b"film" * 5000,
              f"state_versions/_blobs/{voice}": b"voice" * 500,
              "state_versions/p1/v1/manifest.json": b'{"files": {"final.mp4": "ab", "line.wav": "cd"}}',
              "state_versions/p1/v2/manifest.json": b'{"files": {"final.mp4": "ab", "line.wav": "cd"}}'}
    vm = _backup(tmp_path / "vm", "20261003T031500Z", night1)
    first = offsite.push(vm, BUCKET, "backups", keep=14)
    keys = [k for k in offsite._remote(offsite._client(), BUCKET, "backups/data/")]
    assert len([k for k in keys if "/_blobs/" in k]) == 2                  # two contents, four manifest entries
    assert first["uploaded"] == 4 + 1                                      # the four files and the dump

    edited = "ef/" + "ef" * 32
    night2 = dict(night1)
    night2[f"state_versions/_blobs/{edited}"] = b"edited voice" * 500
    night2["state_versions/p1/v3/manifest.json"] = b'{"files": {"final.mp4": "ab", "line.wav": "ef"}}'
    _backup(vm, "20261004T031500Z", night2)
    second = offsite.push(vm, BUCKET, "backups", keep=14)
    assert second["uploaded"] == 2 + 1 and second["deleted"] == 0         # the new blob, the new manifest, the dump
