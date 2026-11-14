"""配置层校验回归用例：结构校验、跨文件引用、自洽性与风险提示。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dreamweave.__main__ import main
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


def _codes(report) -> set[str]:
    return {issue.code for issue in report.errors} | {issue.code for issue in report.warnings}


def test_demo_world_is_clean() -> None:
    report = validate_world(DEMO)
    assert report.ok, [issue.to_dict() for issue in report.errors]
    assert report.warnings == []
    assert set(report.loaded_modules) == {
        "physics",
        "ecology",
        "npc_agent",
        "player_body",
        "visual",
        "audio",
        "haptic",
        "scent",
        "taste",
        "sensory_bus",
        "scenes",
        "narrative",
    }


def test_schema_rejects_wrong_type(world: Path) -> None:
    physics_path = world / "config" / "physics.json"
    physics = _read(physics_path)
    physics["gravity"] = "-9.81"
    _write(physics_path, physics)

    report = validate_world(world)
    assert not report.ok
    assert "SCHEMA" in _codes(report)
    assert any("gravity" in issue.path for issue in report.errors)


def test_schema_rejects_unknown_field(world: Path) -> None:
    physics_path = world / "config" / "physics.json"
    physics = _read(physics_path)
    physics["magic_resistance"] = 1
    _write(physics_path, physics)

    report = validate_world(world)
    assert not report.ok
    assert any("magic_resistance" in issue.message for issue in report.errors)


def test_unknown_species_reference(world: Path) -> None:
    ecology_path = world / "config" / "ecology.json"
    ecology = _read(ecology_path)
    ecology["species"][2]["food_web"]["predators"].append("dragon")
    _write(ecology_path, ecology)

    report = validate_world(world)
    assert "ECOLOGY_UNKNOWN_SPECIES" in _codes(report)


def test_duplicate_species_id(world: Path) -> None:
    ecology_path = world / "config" / "ecology.json"
    ecology = _read(ecology_path)
    ecology["species"].append(dict(ecology["species"][0]))
    _write(ecology_path, ecology)

    report = validate_world(world)
    assert "ECOLOGY_DUPLICATE_SPECIES" in _codes(report)


def test_missing_module_file(world: Path) -> None:
    (world / "config" / "physics.json").unlink()

    report = validate_world(world)
    assert "MODULE_MISSING" in _codes(report)
    assert "physics" not in report.loaded_modules


def test_module_checksum_mismatch(world: Path) -> None:
    manifest_path = world / "world.json"
    manifest = _read(manifest_path)
    manifest["modules"]["physics"]["sha256"] = "0" * 64
    _write(manifest_path, manifest)

    report = validate_world(world)
    assert "MODULE_CHECKSUM" in _codes(report)


def test_path_traversal_is_rejected(world: Path) -> None:
    manifest_path = world / "world.json"
    manifest = _read(manifest_path)
    manifest["modules"]["physics"]["path"] = "../../etc/passwd"
    _write(manifest_path, manifest)

    report = validate_world(world)
    assert "MANIFEST_SCHEMA" in _codes(report)


def test_ambient_outside_comfort_warns(world: Path) -> None:
    physics_path = world / "config" / "physics.json"
    physics = _read(physics_path)
    physics["thermal"]["ambient_temperature_c"] = 45
    _write(physics_path, physics)

    report = validate_world(world)
    assert report.ok
    assert "CROSS_AMBIENT_OUTSIDE_COMFORT" in _codes(report)


def test_npc_relation_must_exist(world: Path) -> None:
    npc_path = world / "config" / "npc_agent.json"
    npc = _read(npc_path)
    npc["npc_roster"][0]["relations"].append({"target": "ghost", "trust": 0.5, "familiarity": 0.5})
    _write(npc_path, npc)

    report = validate_world(world)
    assert "NPC_UNKNOWN_RELATION" in _codes(report)


def test_player_curve_must_be_monotonic(world: Path) -> None:
    player_path = world / "config" / "player_body.json"
    player = _read(player_path)
    player["injury"]["pain_curve"] = [
        {"input": 0, "output": 0},
        {"input": 0.8, "output": 0.4},
        {"input": 0.5, "output": 0.7},
        {"input": 1, "output": 1},
    ]
    _write(player_path, player)

    report = validate_world(world)
    assert "PLAYER_CURVE_NOT_MONOTONIC" in _codes(report)


def test_cli_json_report_exit_code(world: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(world), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"] == {"errors": 0, "warnings": 0}


def test_cli_strict_fails_on_warning(world: Path, capsys: pytest.CaptureFixture[str]) -> None:
    physics_path = world / "config" / "physics.json"
    physics = _read(physics_path)
    physics["thermal"]["ambient_temperature_c"] = 45
    _write(physics_path, physics)

    assert main(["validate", str(world), "--strict"]) == 2
    assert "CROSS_AMBIENT_OUTSIDE_COMFORT" in capsys.readouterr().out


def test_cli_fails_on_error(world: Path) -> None:
    (world / "config" / "player_body.json").unlink()
    assert main(["validate", str(world)]) == 1


def test_schema_files_live_inside_package() -> None:
    from dreamweave.schemas import SCHEMA_DIR

    assert SCHEMA_DIR.parent.name == "dreamweave"
    assert (SCHEMA_DIR / "world_manifest.schema.json").is_file()
    assert (SCHEMA_DIR / "world_scenes.schema.json").is_file()
