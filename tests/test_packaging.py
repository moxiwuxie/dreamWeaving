"""M001-04 / M005-04 .world 打包与导入用例：摘要、版本、路径安全、往返。"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from dreamweave.__main__ import main
from dreamweave.packaging import inspect_package, pack_world, unpack_world
from dreamweave.validator import validate_world

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


@pytest.fixture()
def world(tmp_path: Path) -> Path:
    target = tmp_path / "earth_demo"
    shutil.copytree(DEMO, target)
    return target


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rewrite_entry(package: Path, name: str, payload: bytes) -> None:
    temp = package.with_suffix(".tmp")
    with zipfile.ZipFile(package) as src, zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = payload if info.filename == name else src.read(info)
            dst.writestr(info.filename, data)
    temp.replace(package)


def test_pack_inspect_unpack_roundtrip(world: Path, tmp_path: Path) -> None:
    package = tmp_path / "earth.world"
    packed = pack_world(world, package)
    assert packed["ok"] is True
    assert packed["modules"] == 12
    assert package.is_file()

    inspected = inspect_package(package)
    assert inspected["ok"] is True
    assert inspected["world_id"] == "earth_demo"
    assert "physics" in inspected["modules"]

    restored = tmp_path / "restored"
    unpacked = unpack_world(package, restored)
    assert unpacked["ok"] is True, unpacked["errors"]
    assert validate_world(restored).ok
    assert _read(restored / "config" / "physics.json") == _read(world / "config" / "physics.json")


def test_pack_writes_checksums_into_package(world: Path, tmp_path: Path) -> None:
    package = tmp_path / "a.world"
    assert pack_world(world, package)["ok"]
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("world.json"))
    assert all(len(ref["sha256"]) == 64 for ref in manifest["modules"].values())
    assert len(manifest["checksum"]) == 64


def test_pack_leaves_working_directory_editable(world: Path, tmp_path: Path) -> None:
    before = _read(world / "world.json")
    assert pack_world(world, tmp_path / "a.world")["ok"]
    assert _read(world / "world.json") == before

    path = world / "config" / "physics.json"
    data = _read(path)
    data["gravity"] = -3.7
    _write(path, data)
    assert validate_world(world).ok


def test_pack_refuses_invalid_world(world: Path, tmp_path: Path) -> None:
    path = world / "config" / "physics.json"
    data = _read(path)
    data["gravity"] = "low"
    _write(path, data)

    package = tmp_path / "bad.world"
    result = pack_world(world, package)
    assert result["ok"] is False
    assert result["errors"]
    assert not package.exists()


def test_tampered_module_is_rejected(world: Path, tmp_path: Path) -> None:
    package = tmp_path / "earth.world"
    assert pack_world(world, package)["ok"]

    _rewrite_entry(package, "config/physics.json", b'{"schema_version": "0.1.0"}')

    result = unpack_world(package, tmp_path / "restored")
    assert result["ok"] is False
    assert any(issue["code"] == "CHECKSUM_MISMATCH" for issue in result["errors"])


def test_tampered_manifest_is_rejected(world: Path, tmp_path: Path) -> None:
    package = tmp_path / "earth.world"
    assert pack_world(world, package)["ok"]

    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("world.json"))
    manifest["world_name"] = "被篡改的世界"
    _rewrite_entry(package, "world.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))

    result = unpack_world(package, tmp_path / "restored")
    assert result["ok"] is False
    assert any(issue["code"] == "MANIFEST_CHECKSUM" for issue in result["errors"])


def test_version_incompatible_is_rejected(world: Path, tmp_path: Path) -> None:
    manifest_path = world / "world.json"
    manifest = _read(manifest_path)
    manifest["engine"]["min_loader_version"] = "9.9.9"
    _write(manifest_path, manifest)

    package = tmp_path / "future.world"
    assert pack_world(world, package)["ok"]

    inspected = inspect_package(package)
    assert inspected["ok"] is False
    assert inspected["errors"][0]["code"] == "LOADER_TOO_OLD"

    result = unpack_world(package, tmp_path / "restored")
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "LOADER_TOO_OLD"


def test_zip_path_traversal_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "evil.world"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("../evil.txt", "pwned")
        archive.writestr("world.json", json.dumps({"world_id": "evil"}))

    result = unpack_world(package, tmp_path / "restored")
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "ZIP_UNSAFE_PATH"
    assert not (tmp_path / "evil.txt").exists()


def test_dest_must_be_empty(world: Path, tmp_path: Path) -> None:
    package = tmp_path / "earth.world"
    assert pack_world(world, package)["ok"]

    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "keep.txt").write_text("x", encoding="utf-8")

    result = unpack_world(package, dest)
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "DEST_NOT_EMPTY"


def test_cli_pack_and_inspect(world: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "cli.world"
    assert main(["pack", str(world), "-o", str(package)]) == 0
    assert "checksum" in capsys.readouterr().out

    assert main(["inspect", str(package), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["world_id"] == "earth_demo"
