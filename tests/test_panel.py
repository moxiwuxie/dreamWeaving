"""M005 面板反射：Schema -> 控件树。"""

import json

import pytest

from dreamweave import build_panel
from dreamweave.__main__ import main
from dreamweave.schemas import MODULE_SCHEMAS


def _flatten(fields):
    flat = {}
    for item in fields:
        flat[item["path"]] = item
        flat.update(_flatten(item.get("fields") or []))
        flat.update(_flatten(item.get("item_fields") or []))
    return flat


def test_all_modules_build():
    for module in (*MODULE_SCHEMAS, "manifest"):
        panel = build_panel(module)
        assert panel["fields"], module
        assert panel["module"] == module


def test_number_bounds_and_required():
    fields = _flatten(build_panel("physics")["fields"])
    gravity = fields["/gravity"]
    assert gravity["widget"] == "number"
    assert gravity["minimum"] == -100 and gravity["maximum"] == 100
    assert gravity["required"] is True
    assert "重力加速度" in gravity["description"]


def test_ref_to_normalized_becomes_slider():
    fields = _flatten(build_panel("physics")["fields"])
    turbulence = fields["/fluid/turbulence_scale"]
    assert turbulence["widget"] == "slider"
    assert (turbulence["minimum"], turbulence["maximum"]) == (0, 1)


def test_enum_becomes_select():
    fields = _flatten(build_panel("physics")["fields"])
    preset = fields["/preset"]
    assert preset["widget"] == "select"
    assert preset["options"] == ["earth", "moon", "mars", "zero_g", "fantasy", "custom"]


def test_const_becomes_readonly():
    fields = _flatten(build_panel("physics")["fields"])
    version = fields["/schema_version"]
    assert version["widget"] == "readonly"
    assert version["value"] == "0.1.0"


def test_optional_group_keeps_children():
    fields = _flatten(build_panel("physics")["fields"])
    assert fields["/deformation"]["required"] is False
    assert fields["/deformation/youngs_modulus_gpa"]["minimum"] == 0.0001


def test_array_of_objects_exposes_item_fields():
    fields = _flatten(build_panel("npc_agent")["fields"])
    goods = fields["/economy/goods"]
    assert goods["widget"] == "list"
    assert goods["max_items"] == 512
    assert fields["/economy/goods/[]/base_price"]["minimum"] == 0


def test_nested_ref_inside_common_document():
    fields = _flatten(build_panel("visual")["fields"])
    curve = fields["/exposure/adapt_curve"]
    assert curve["widget"] == "list"
    assert (curve["min_items"], curve["max_items"]) == (2, 32)
    assert fields["/exposure/adapt_curve/[]/input"]["widget"] == "number"


def test_map_widget_uses_property_names():
    fields = _flatten(build_panel("visual")["fields"])
    vfx = fields["/status_vfx_map"]
    assert vfx["widget"] == "map"
    assert vfx["value_widget"] == "text"
    assert "^[a-z]" in vfx["key_pattern"]


def test_single_value_enum_is_readonly():
    fields = _flatten(build_panel("manifest")["fields"])
    voxel = fields["/terrain/voxel_size_m"]
    assert voxel["widget"] == "readonly"
    assert voxel["value"] == 0.1


def test_unknown_module_rejected():
    with pytest.raises(KeyError):
        build_panel("no_such_module")


def test_cli_panel_json(capsys):
    assert main(["panel", "npc_agent", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["module"] == "npc_agent"
    assert payload["fields"][0]["key"] == "schema_version"


def test_cli_panel_text(capsys):
    assert main(["panel", "player_body"]) == 0
    output = capsys.readouterr().out
    assert "[PANEL] player_body" in output
    assert "必填" in output
