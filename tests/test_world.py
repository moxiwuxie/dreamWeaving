"""M006 接口层用例：查询、修改回滚、自然语言翻译、NPC 上下文。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dreamweave.__main__ import main
from dreamweave.npc_context import build_npc_context
from dreamweave.translator import translate
from dreamweave.world import WorldError, WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


@pytest.fixture()
def session(tmp_path: Path) -> WorldSession:
    target = tmp_path / "earth_demo"
    shutil.copytree(DEMO, target)
    return WorldSession(target)


def _physics(session: WorldSession) -> dict:
    return json.loads((session.root / "config" / "physics.json").read_text(encoding="utf-8"))


def test_manifest_module_path_cannot_escape_root(tmp_path: Path) -> None:
    world = tmp_path / "world"
    shutil.copytree(DEMO, world)
    manifest_path = world / "world.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["modules"]["physics"]["path"] = "../../evil.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(WorldError):
        WorldSession(world)


def test_query_apis(session: WorldSession) -> None:
    assert session.get_world_physics()["gravity"] == -9.81
    npcs = session.query_npc_list()
    assert {npc["id"] for npc in npcs} == {"guard_lin", "merchant_zhao", "elder_qiu"}
    assert len(session.query_npc_list("llm_core")) == 1

    biomes = session.query_biome_stats()
    forest = next(item for item in biomes if item["biome"] == "temperate_forest")
    assert forest["species_count"] == 5
    assert "oak_tree" in forest["species"]

    weather = session.get_weather()
    assert weather["base_temperature_c"] == 15
    assert {event["kind"] for event in weather["events"]} == {"rain", "storm", "snow"}


def test_set_physics_param_writes_and_undoes(session: WorldSession) -> None:
    result = session.set_physics_param("gravity", -3.7)
    assert result.accepted
    assert _physics(session)["gravity"] == -3.7

    assert session.undo() is True
    assert _physics(session)["gravity"] == -9.81
    assert session.undo() is False


def test_dry_run_does_not_write(session: WorldSession) -> None:
    result = session.set_physics_param("gravity", -3.7, dry_run=True)
    assert result.accepted
    assert _physics(session)["gravity"] == -9.81
    assert session.undo() is False


def test_invalid_patch_is_rejected_and_rolled_back(session: WorldSession) -> None:
    result = session.set_physics_param("gravity", -500)
    assert not result.accepted
    assert result.errors
    assert _physics(session)["gravity"] == -9.81
    assert session.undo() is False


def test_conflicting_rule_is_rejected(session: WorldSession) -> None:
    result = session.apply_operations(
        [{"op": "set", "path": "npc_agent.layers.llm_core.enabled", "value": False}]
    )
    assert not result.accepted
    assert any(issue["code"] == "NPC_LAYER_DISABLED" for issue in result.errors)


def test_unknown_field_is_rejected(session: WorldSession) -> None:
    result = session.apply_operations([{"op": "set", "path": "physics.magic", "value": 1}])
    assert not result.accepted
    assert result.errors[0]["code"] == "PATCH_INVALID"


def test_spawn_npc_appends_and_validates(session: WorldSession) -> None:
    npc = {
        "id": "hunter_ma",
        "name": "马猎户",
        "layer": "mass_life",
        "schedule_template": "town_day",
        "persona": {
            "traits": {"openness": 0.4, "conscientiousness": 0.7, "extraversion": 0.5, "agreeableness": 0.6, "neuroticism": 0.4},
            "goals": ["守住猎场"],
            "speech_style": "话少",
        },
        "memory": {"capacity": 1000, "seed_events": [{"day": 5, "summary": "第一次独自出猎", "salience": 0.8}]},
        "relations": [{"target": "guard_lin", "trust": 0.3, "familiarity": 0.4}],
    }
    result = session.spawn_npc(npc)
    assert result.accepted
    assert any(item["id"] == "hunter_ma" for item in session.query_npc_list())

    broken = dict(npc, id="hunter_ma")
    assert not session.spawn_npc(broken).accepted


def test_trigger_weather_event(session: WorldSession) -> None:
    triggered = session.trigger_weather_event("summer_storm")
    assert triggered["triggered"] is True
    assert triggered["event"]["kind"] == "storm"
    assert session.trigger_weather_event("meteor").get("triggered") is False


def test_translate_numeric_intent(session: WorldSession) -> None:
    result = translate(session, "把重力改为 -3.7")
    assert result["accepted"] is True
    assert result["operations"][0]["path"] == "physics.gravity"
    assert result["operations"][0]["value"] == -3.7


def test_translate_rejects_out_of_range(session: WorldSession) -> None:
    result = translate(session, "把空气密度改为 999")
    assert result["accepted"] is False
    assert result["errors"]


def test_translate_price_intent(session: WorldSession) -> None:
    result = translate(session, "把木材价格改为 8")
    assert result["accepted"] is True
    operation = result["operations"][0]
    assert operation["path"].endswith(".base_price")
    assert operation["value"] == 8


def test_translate_weather_probability(session: WorldSession) -> None:
    result = translate(session, "把暴雨概率改为 20%")
    assert result["accepted"] is True
    assert result["operations"][0]["value"] == 0.2
    assert "weather_events" in result["operations"][0]["path"]


def test_translate_unsupported_prompt(session: WorldSession) -> None:
    result = translate(session, "给世界加一个会飞的城堡")
    assert result["accepted"] is False
    assert "无法解析" in result["message"]


def test_npc_context_contains_persona_and_schedule(session: WorldSession) -> None:
    payload = build_npc_context(session, "elder_qiu")
    context = payload["context"]
    assert context["npc"]["name"] == "秋长老"
    assert context["memory"][0]["salience"] == 1.0
    assert len(context["schedule"]) > 0
    assert "禁止纵火" in payload["prompt"]
    assert "秋长老" in payload["prompt"]
    assert "temperate_forest" not in payload["prompt"]


def test_npc_context_unknown_id(session: WorldSession) -> None:
    with pytest.raises(KeyError):
        build_npc_context(session, "nobody")


def test_cli_ask_apply_writes(session: WorldSession, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ask", str(session.root), "把重力改为 -5", "--apply"]) == 0
    assert "APPLIED" in capsys.readouterr().out
    assert _physics(session)["gravity"] == -5
