"""M001-03 世界模板脚手架：生成即合法。"""

import json

import pytest

from dreamweave import create_world, validate_world
from dreamweave.__main__ import main
from dreamweave.templates import TEMPLATES


@pytest.mark.parametrize("template", TEMPLATES)
def test_generated_world_is_valid(tmp_path, template):
    result = create_world(tmp_path / template, f"{template}_demo", template=template)
    assert result["ok"], result["errors"]
    assert result["errors"] == []
    report = validate_world(tmp_path / template)
    assert report.ok and report.errors == []


@pytest.mark.parametrize("template", ["earth", "fantasy"])
def test_full_templates_have_no_warnings(tmp_path, template):
    result = create_world(tmp_path / template, f"{template}_demo", template=template)
    assert result["warnings"] == []


def test_minimal_template_keeps_core_modules_only(tmp_path):
    result = create_world(tmp_path / "blank", "blank_demo", template="blank")
    assert result["modules"] == 4
    manifest = json.loads((tmp_path / "blank" / "world.json").read_text(encoding="utf-8"))
    assert sorted(manifest["modules"]) == ["ecology", "npc_agent", "physics", "player_body"]
    assert manifest["template"] == "blank"


def test_full_template_writes_sensory_modules(tmp_path):
    create_world(tmp_path / "earth", "earth_demo", template="earth")
    manifest = json.loads((tmp_path / "earth" / "world.json").read_text(encoding="utf-8"))
    assert len(manifest["modules"]) == 12
    for module in manifest["modules"].values():
        assert (tmp_path / "earth" / module["path"]).is_file()


def test_refuses_non_empty_directory(tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    result = create_world(target, "some_world")
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "DEST_NOT_EMPTY"
    assert (target / "keep.txt").is_file()


def test_force_overwrites(tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "world.json").write_text("{}", encoding="utf-8")
    result = create_world(target, "some_world", overwrite=True)
    assert result["ok"], result["errors"]


def test_invalid_world_id_rejected(tmp_path):
    result = create_world(tmp_path / "bad", "Bad-World")
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "INVALID_WORLD_ID"
    assert not (tmp_path / "bad").exists()


def test_unknown_template_rejected(tmp_path):
    with pytest.raises(KeyError):
        create_world(tmp_path / "x", "some_world", template="nope")


def test_cli_init_json(tmp_path, capsys):
    code = main(["init", "cli_world", "-o", str(tmp_path / "w"), "--template", "blank", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["world_id"] == "cli_world"
    assert (tmp_path / "w" / "world.json").is_file()
