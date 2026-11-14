"""M007 剧情内核：结构压力、AI 上下文与大纲写入。"""

import json
import shutil
from pathlib import Path

import pytest

from dreamweave import (
    advance_narrative,
    build_story_context,
    ecology_deltas,
    environment_deltas,
    escalate_stage,
    handle_story_outline,
    narrative_pressures,
    outline_quality_errors,
    validate_world,
)
from dreamweave.__main__ import main
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _copy_demo(tmp_path: Path) -> Path:
    target = tmp_path / "world"
    shutil.copytree(DEMO, target)
    return target


def _outline(story_key: str) -> dict:
    return {
        "story_key": story_key,
        "title": "税吏进村",
        "logline": "旱季里，税吏带着新秤进村，农户与王国第一次正面相对。",
        "tensions": [
            {
                "id": "new_tax_pressure",
                "name": "新秤旧税",
                "parties": [
                    {"kind": "nation", "ref": "river_kingdom"},
                    {"kind": "class", "ref": "farmers"},
                ],
                "stake": "food",
                "stage": "emerging",
                "intensity": 0.5,
                "triggers": ["税吏换秤", "旱情加重"],
            }
        ],
        "arcs": [
            {
                "id": "qiu_new_scale",
                "title": "新秤",
                "npc_ref": "elder_qiu",
                "nation_ref": "river_kingdom",
                "class_ref": "farmers",
                "desire": "让村里人相信秤是准的",
                "obstacle": "秤砣被税吏做了手脚",
                "flaw": "把怀疑藏在心里",
                "secret": "他年轻时也做过税吏",
                "stakes": "community",
                "stage": "setup",
                "tags": ["税赋", "信任"],
            }
        ],
        "chronicle": [
            {
                "id": "tax_collector_arrives",
                "day": 2,
                "summary": "税吏带着新秤进村，农户围观不肯散去。",
                "participants": ["river_kingdom", "farmers"],
                "tension_ref": "new_tax_pressure",
            }
        ],
    }


def test_pressures_rank_nation_class_and_tension():
    pressures = narrative_pressures(WorldSession(DEMO), limit=10)
    kinds = {item["kind"] for item in pressures}
    assert kinds == {"nation", "class", "tension"}
    assert [item["score"] for item in pressures] == sorted((item["score"] for item in pressures), reverse=True)
    assert any(item["id"] == "farmers" for item in pressures)


def test_story_context_is_focused_and_unique_per_day():
    session = WorldSession(DEMO)
    first = build_story_context(session, nation_id="river_kingdom", npc_id="guard_lin", day=2)
    second = build_story_context(session, nation_id="river_kingdom", npc_id="guard_lin", day=3)
    assert first["story_key"] != second["story_key"]
    assert first["context"]["focus"]["nation"]["id"] == "river_kingdom"
    assert first["context"]["focus"]["npc"]["id"] == "guard_lin"
    assert first["story_key"] in first["prompt"]
    assert "必须遵守的现实因果律" in first["prompt"]
    assert "禁止重复" in first["prompt"]


def test_unknown_focus_is_rejected():
    session = WorldSession(DEMO)
    with pytest.raises(ValueError):
        build_story_context(session, nation_id="nowhere")


def test_outline_dry_run_does_not_write(tmp_path):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    before = len(session.modules["narrative"]["tensions"])

    result = handle_story_outline(session, _outline("0123456789abcdef"), dry_run=True)
    assert result["accepted"] is True, result["errors"]
    assert result["operations"]
    assert len(session.modules["narrative"]["tensions"]) == before
    assert len(WorldSession(world).modules["narrative"]["tensions"]) == before


def test_outline_apply_updates_narrative_and_keeps_world_valid(tmp_path):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    result = handle_story_outline(session, _outline("0123456789abcdef"), dry_run=False)
    assert result["accepted"] is True, result["errors"]
    assert validate_world(world).ok

    reloaded = WorldSession(world).modules["narrative"]
    assert any(item["id"] == "new_tax_pressure" for item in reloaded["tensions"])
    assert any(item["id"] == "qiu_new_scale" for item in reloaded["arcs"])
    assert any(item["id"] == "tax_collector_arrives" for item in reloaded["chronicle"])


def test_outline_with_duplicate_id_is_rejected(tmp_path):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    outline = _outline("0123456789abcdef")
    outline["tensions"][0]["id"] = "water_dispute"

    result = handle_story_outline(session, outline)
    assert result["accepted"] is False
    assert any(issue["code"] == "NARRATIVE_DUPLICATE_ID" for issue in result["errors"])


def test_outline_with_unknown_npc_is_rejected(tmp_path):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    outline = _outline("0123456789abcdef")
    outline["arcs"][0]["npc_ref"] = "ghost"

    result = handle_story_outline(session, outline)
    assert result["accepted"] is False
    assert any(issue["code"] == "NARRATIVE_UNKNOWN_NPC" for issue in result["errors"])


def test_outline_schema_violation_is_rejected():
    session = WorldSession(DEMO)
    result = handle_story_outline(session, {"story_key": "bad"})
    assert result["accepted"] is False
    assert result["errors"][0]["code"] == "OUTLINE_SCHEMA"


def test_cli_narrative_flow(capsys):
    assert main(["narrative", "pressures", str(DEMO), "--json"]) == 0
    pressures = json.loads(capsys.readouterr().out)
    assert pressures and {"score", "kind", "drivers"} <= set(pressures[0])

    assert main(["narrative", "context", str(DEMO), "--nation", "river_kingdom", "--json"]) == 0
    context = json.loads(capsys.readouterr().out)
    assert len(context["story_key"]) == 16

    assert main(["narrative", "context", str(DEMO), "--nation", "nowhere"]) == 2
    capsys.readouterr()

    assert main(["narrative", "outline", str(DEMO), json.dumps(_outline("0123456789abcdef"))]) == 0
    assert "DRY-RUN" in capsys.readouterr().out


def test_escalate_stage_only_moves_up():
    assert escalate_stage("dormant", 0.3) == "emerging"
    assert escalate_stage("escalating", 0.66) == "open"
    assert escalate_stage("open", 0.9) == "crisis"
    assert escalate_stage("open", 0.1) == "open"
    assert escalate_stage("resolution", 1.0) == "resolution"


def test_environment_deltas_follow_resource_chain():
    tensions = WorldSession(DEMO).modules["narrative"]["tensions"]
    deltas = {item["tension_id"]: item for item in environment_deltas(tensions, ["heatwave"])}

    assert deltas["water_dispute"]["to_intensity"] == 0.71
    assert deltas["water_dispute"]["to_stage"] == "open"
    assert deltas["grain_tax"]["to_stage"] == "escalating"
    assert environment_deltas(tensions, ["aurora"]) == []


class _FakeSimulation:
    """固定返回一次热浪，用于验证环境 -> 结构压力的接线。"""

    def __init__(self, session, *, day=1, seed=7):
        self.absolute_minute = (day - 1) * 1440
        self.events = [{"minute": (day - 1) * 1440 + 30, "type": "weather", "value": "heatwave"}]

    def tick(self, minutes=60, step=10):
        return {}

    def to_state(self):
        return {"absolute_minute": self.absolute_minute}


def test_advance_narrative_links_weather_to_tensions(tmp_path, monkeypatch):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    monkeypatch.setattr("dreamweave.narrative.Simulation", _FakeSimulation)

    result = advance_narrative(session, day_start=3, dry_run=False)

    assert result["accepted"] is True, result["errors"]
    assert result["weather"] == [{"kind": "heatwave", "day": 3}]
    assert result["chronicle"][0]["id"] == "env_heatwave_3"
    reloaded = WorldSession(world).modules["narrative"]
    water = next(item for item in reloaded["tensions"] if item["id"] == "water_dispute")
    assert water["intensity"] == 0.71
    assert water["stage"] == "open"
    assert any(item["id"] == "env_heatwave_3" for item in reloaded["chronicle"])
    assert validate_world(world).ok


def test_advance_narrative_is_idempotent(tmp_path, monkeypatch):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    monkeypatch.setattr("dreamweave.narrative.Simulation", _FakeSimulation)

    first = advance_narrative(session, day_start=3, dry_run=False)
    assert first["accepted"] is True, first["errors"]
    assert first["changes"]

    second = advance_narrative(session, day_start=3, dry_run=False)
    assert second["accepted"] is True, second["errors"]
    assert second["changes"] == []
    assert second["chronicle"] == []
    assert "已写入" in second["message"]

    water = next(
        item for item in WorldSession(world).modules["narrative"]["tensions"] if item["id"] == "water_dispute"
    )
    assert water["intensity"] == 0.71
    assert water["stage"] == "open"


class _FakeShockSimulation:
    """固定返回一次降雪、两人避险与一条生态衰退，用于验证社会/生态 -> 剧情接线。"""

    def __init__(self, session, *, day=1, seed=7):
        base = (day - 1) * 1440
        self.absolute_minute = base
        self.events = [
            {"minute": base + 30, "type": "weather", "value": "snow"},
            {"minute": base + 60, "type": "npc_activity", "value": "merchant_zhao shelter"},
            {"minute": base + 70, "type": "npc_activity", "value": "elder_qiu shelter"},
            {"minute": base + 80, "type": "npc_activity", "value": "merchant_zhao work"},
            {"minute": base + 90, "type": "ecology", "value": "grass stable→declining"},
        ]

    def tick(self, minutes=60, step=10):
        return {}

    def to_state(self):
        return {"absolute_minute": self.absolute_minute}


def test_ecology_deltas_reach_food_chain():
    tensions = WorldSession(DEMO).modules["narrative"]["tensions"]
    deltas = {item["tension_id"]: item for item in ecology_deltas(tensions, ["declining"])}

    assert deltas["grain_tax"]["to_intensity"] == 0.46
    assert "water_dispute" not in deltas
    assert ecology_deltas(tensions, []) == []


def test_advance_narrative_links_ecology_and_shelter(tmp_path, monkeypatch):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    monkeypatch.setattr("dreamweave.narrative.Simulation", _FakeShockSimulation)

    result = advance_narrative(session, day_start=2, dry_run=False)

    assert result["accepted"] is True, result["errors"]
    assert {item["id"] for item in result["chronicle"]} == {
        "env_snow_2",
        "env_ecology_grass_2",
        "env_shelter_2",
    }
    changes = {item["tension_id"]: item for item in result["changes"]}
    assert changes["grain_tax"]["to_intensity"] == 0.52
    assert changes["grain_tax"]["to_stage"] == "escalating"

    shelter = next(item for item in result["chronicle"] if item["id"] == "env_shelter_2")
    assert shelter["participants"] == ["elder_qiu", "merchant_zhao"]
    assert "2 名居民" in shelter["summary"]

    assert validate_world(world).ok
    reloaded = WorldSession(world).modules["narrative"]
    stored_ids = {item["id"] for item in reloaded["chronicle"]}
    assert {"env_snow_2", "env_ecology_grass_2", "env_shelter_2"} <= stored_ids

    second = advance_narrative(session, day_start=2, dry_run=False)
    assert second["chronicle"] == []
    assert "已写入" in second["message"]


class _FakeBoundarySimulation:
    """事件恰好落在推进窗口右边界，属于下一天，不应计入本次推进。"""

    def __init__(self, session, *, day=1, seed=7):
        self.absolute_minute = (day - 1) * 1440
        self.events = [{"minute": day * 1440, "type": "weather", "value": "snow"}]

    def tick(self, minutes=60, step=10):
        return {}

    def to_state(self):
        return {"absolute_minute": self.absolute_minute}


def test_advance_narrative_ignores_next_day_boundary_event(tmp_path, monkeypatch):
    world = _copy_demo(tmp_path)
    monkeypatch.setattr("dreamweave.narrative.Simulation", _FakeBoundarySimulation)

    result = advance_narrative(WorldSession(world), day_start=2, days=1)

    assert result["weather"] == []
    assert "没有环境或社会冲击" in result["message"]


def test_outline_quality_errors_require_pressure_stake_and_unique_title():
    context = {
        "pressures": [{"suggested_stake": "food"}],
        "arcs": [{"title": "城门下的秤"}],
        "recent_chronicle": [],
    }

    assert [
        item["code"]
        for item in outline_quality_errors({"title": "新故事", "tensions": [{"stake": "honor"}]}, context)
    ] == ["OUTLINE_PRESSURE"]
    assert [
        item["code"]
        for item in outline_quality_errors(
            {"title": "城门下的秤", "tensions": [{"stake": "food"}]}, context
        )
    ] == ["OUTLINE_TITLE_REUSED"]
    assert outline_quality_errors({"title": "新故事", "tensions": [{"stake": "food"}]}, context) == []


def test_advance_narrative_resumes_from_state(tmp_path):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)

    first = advance_narrative(session, days=1, day_start=1, dry_run=True)
    assert first["state"]["absolute_minute"] == 1440

    second = advance_narrative(session, days=1, dry_run=True, state=first["state"])
    assert second["state"]["absolute_minute"] == 2880
    assert validate_world(world).ok


def test_cli_narrative_advance_state_chain(tmp_path):
    world = _copy_demo(tmp_path)
    state_path = tmp_path / "sim_state.json"

    assert main(["narrative", "advance", str(world), "--days", "1", "--state-out", str(state_path)]) == 0
    assert state_path.is_file()

    assert (
        main(
            [
                "narrative",
                "advance",
                str(world),
                "--days",
                "1",
                "--state-in",
                str(state_path),
                "--state-out",
                str(state_path),
            ]
        )
        == 0
    )
    assert json.loads(state_path.read_text(encoding="utf-8"))["absolute_minute"] == 2880


def test_cli_narrative_advance_dry_run(capsys, monkeypatch):
    monkeypatch.setattr("dreamweave.narrative.Simulation", _FakeSimulation)

    assert main(["narrative", "advance", str(DEMO), "--day", "4", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is True
    assert payload["chronicle"][0]["id"] == "env_heatwave_4"
    assert payload["changes"]
