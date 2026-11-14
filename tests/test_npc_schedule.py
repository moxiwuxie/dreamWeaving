"""NPC 日程运行时：模板解析、地点映射、过滤与 CLI 输出。"""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from dreamweave import build_npc_context, build_schedule, schedule_at, validate_world
from dreamweave.__main__ import main
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _copy_demo(tmp_path: Path) -> Path:
    target = tmp_path / "world"
    shutil.copytree(DEMO, target)
    return target


def test_schedule_at_midnight_resolves_home_and_mapped_locations(tmp_path):
    session = SimpleNamespace(modules={"npc_agent": json.loads((DEMO / "config" / "npc_agent.json").read_text(encoding="utf-8"))})
    data = build_schedule(session, day=1, minute=0)

    assert data["time"] == "00:00"
    assert data["count"] == 3
    by_id = {npc["npc_id"]: npc for npc in data["npcs"]}

    guard = by_id["guard_lin"]
    assert guard["activity"] == "patrol"
    assert guard["location"] == "gate"
    assert guard["scene_id"] == "city_core"
    assert guard["ends_in_min"] == 360

    merchant = by_id["merchant_zhao"]
    assert merchant["activity"] == "sleep"
    assert merchant["location"] == "home"
    assert merchant["scene_id"] == "city_core"
    assert merchant["home_scene_id"] == "city_core"

    elder = by_id["elder_qiu"]
    assert elder["scene_id"] == "farmland"
    assert elder["ends_in_min"] == 390


def test_location_map_overrides_home_scene(tmp_path):
    session = SimpleNamespace(modules={"npc_agent": json.loads((DEMO / "config" / "npc_agent.json").read_text(encoding="utf-8"))})
    data = build_schedule(session, minute=390)

    by_id = {npc["npc_id"]: npc for npc in data["npcs"]}
    assert by_id["merchant_zhao"]["activity"] == "eat"
    assert by_id["merchant_zhao"]["scene_id"] == "city_core"
    assert by_id["elder_qiu"]["scene_id"] == "city_core"


def test_schedule_filters_by_scene_activity_and_layer():
    session = SimpleNamespace(modules={"npc_agent": json.loads((DEMO / "config" / "npc_agent.json").read_text(encoding="utf-8"))})

    workers = build_schedule(session, minute=480, activity="work")
    assert [npc["npc_id"] for npc in workers["npcs"]] == ["elder_qiu", "merchant_zhao"]

    farmland = build_schedule(session, minute=480, scene_id="farmland")
    assert farmland["count"] == 0

    core = build_schedule(session, minute=480, layer="llm_core")
    assert [npc["npc_id"] for npc in core["npcs"]] == ["elder_qiu"]


def test_schedule_rejects_invalid_time():
    session = SimpleNamespace(modules={"npc_agent": {}})
    with pytest.raises(ValueError, match="day"):
        schedule_at(session, day=0)
    with pytest.raises(ValueError, match="minute"):
        schedule_at(session, minute=-1)
    with pytest.raises(ValueError, match="minute"):
        schedule_at(session, minute=1440)
    with pytest.raises(ValueError, match="npc_agent"):
        schedule_at(SimpleNamespace(modules={}))


def test_npc_context_injects_current_schedule(tmp_path):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    payload = build_npc_context(session, "elder_qiu", minute=390)
    current = payload["context"]["current"]
    assert current["activity"] == "eat"
    assert current["scene_id"] == "city_core"
    assert payload["context"]["environment"]["scene"]["id"] == "city_core"
    assert "第 1 天 06:30：eat @ tavern" in payload["prompt"]


def test_cli_npc_schedule_json(capsys):
    code = main(["npc-schedule", str(DEMO), "--minute", "480", "--activity", "work", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert payload["filters"]["activity"] == "work"


def test_cli_npc_schedule_text(capsys):
    code = main(["npc-schedule", str(DEMO), "--minute", "0", "--layer", "llm_core"])
    assert code == 0
    out = capsys.readouterr().out
    assert "第 1 天 00:00" in out
    assert "elder_qiu" in out


def test_cli_npc_schedule_reports_bad_minute(capsys):
    code = main(["npc-schedule", str(DEMO), "--minute", "1500"])
    assert code == 2
    assert "minute" in capsys.readouterr().err


def test_validator_rejects_unknown_location_scene(tmp_path):
    world = _copy_demo(tmp_path)
    path = world / "config" / "npc_agent.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["location_scene_map"]["tavern"] = "nowhere"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = validate_world(world)
    assert any(issue.code == "NPC_LOCATION_UNKNOWN_SCENE" for issue in report.errors)
