"""五感模块校验 + NPC 回复闭环用例。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dreamweave.npc_intent import handle_npc_reply
from dreamweave.translator import translate
from dreamweave.validator import validate_world
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


@pytest.fixture()
def world(tmp_path: Path) -> Path:
    target = tmp_path / "earth_demo"
    shutil.copytree(DEMO, target)
    return target


@pytest.fixture()
def session(world: Path) -> WorldSession:
    return WorldSession(world)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _codes(report) -> set[str]:
    return {issue.code for issue in report.errors} | {issue.code for issue in report.warnings}


# ---------- M002 五感 ----------


def test_haptic_unknown_wave(world: Path) -> None:
    path = world / "config" / "haptic.json"
    data = _read(path)
    data["event_wave_map"][0]["wave_id"] = "ghost_wave"
    _write(path, data)

    assert "HAPTIC_UNKNOWN_WAVE" in _codes(validate_world(world))


def test_scent_unknown_reference_from_player(world: Path) -> None:
    path = world / "config" / "player_body.json"
    data = _read(path)
    data["status_effects"][2]["feedback"]["scent"] = "scent_unknown"
    _write(path, data)

    assert "SCENT_UNKNOWN_REF" in _codes(validate_world(world))


def test_scent_without_fallback_warns(world: Path) -> None:
    path = world / "config" / "scent.json"
    data = _read(path)
    del data["fallback"]
    _write(path, data)

    assert "SCENT_NO_FALLBACK" in _codes(validate_world(world))


def test_visual_unknown_status_and_mismatch(world: Path) -> None:
    path = world / "config" / "visual.json"
    data = _read(path)
    data["status_vfx_map"]["legacy_status"] = "vfx_legacy"
    data["status_vfx_map"]["hypothermia"] = "vfx_other"
    _write(path, data)

    codes = _codes(validate_world(world))
    assert "CROSS_VFX_UNKNOWN" in codes
    assert "CROSS_VFX_MISMATCH" in codes


def test_taste_duplicate_food(world: Path) -> None:
    path = world / "config" / "taste.json"
    data = _read(path)
    data["food_bindings"].append(dict(data["food_bindings"][0]))
    _write(path, data)

    assert "TASTE_DUPLICATE_FOOD" in _codes(validate_world(world))


# ---------- M006 多意图与 NPC 闭环 ----------


def test_multi_intent_translation(session: WorldSession) -> None:
    result = translate(session, "把重力改为 -3.7，把空气密度改为 1.2，把混响改为洞穴")
    assert result["accepted"] is True
    assert [op["path"] for op in result["operations"]] == [
        "physics.gravity",
        "physics.air_density",
        "audio.reverb_preset",
    ]
    assert result["operations"][2]["value"] == "cave"


def test_multi_intent_is_atomic(session: WorldSession) -> None:
    result = translate(session, "把重力改为 -3.7，把空气密度改为 999", dry_run=False)
    assert result["accepted"] is False
    assert _read(session.root / "config" / "physics.json")["gravity"] == -9.81
    assert session.undo() is False


def test_hardware_and_percent_intents(session: WorldSession) -> None:
    result = translate(session, "开启 OVR 嗅觉硬件，把感染概率改为 5%")
    assert result["accepted"] is True
    assert result["operations"][0]["path"] == "scent.hardware.ovr_omara.enabled"
    assert result["operations"][0]["value"] is True
    assert result["operations"][1]["value"] == 0.05


def _reply(npc_id: str, action: dict, dialogue: str = "……") -> dict:
    return {
        "schema_version": "0.1.0",
        "npc_id": npc_id,
        "dialogue": dialogue,
        "confidence": 0.8,
        "action": action,
    }


def test_npc_world_edit_accepted(session: WorldSession) -> None:
    payload = _reply("elder_qiu", {"type": "world_edit", "instruction": "把环境温度改为 12"})
    result = handle_npc_reply(session, "elder_qiu", payload)
    assert result["accepted"] is True
    assert result["operations"][0]["path"] == "physics.thermal.ambient_temperature_c"


def test_npc_world_edit_denied_for_mass_life(session: WorldSession) -> None:
    payload = _reply("guard_lin", {"type": "world_edit", "instruction": "把重力改为 -1"})
    result = handle_npc_reply(session, "guard_lin", payload)
    assert result["accepted"] is False
    assert result["errors"][0]["code"] == "NPC_NOT_AUTHORIZED"


def test_npc_passive_action_does_not_touch_world(session: WorldSession) -> None:
    payload = _reply("merchant_zhao", {"type": "trade", "target": "bread"})
    result = handle_npc_reply(session, "merchant_zhao", payload)
    assert result["accepted"] is True
    assert result["operations"] == []
    assert session.undo() is False


def test_npc_reply_schema_requires_instruction(session: WorldSession) -> None:
    payload = _reply("elder_qiu", {"type": "world_edit"})
    result = handle_npc_reply(session, "elder_qiu", payload)
    assert result["accepted"] is False
    assert result["errors"][0]["code"] == "NPC_REPLY_SCHEMA"


def test_npc_reply_id_mismatch(session: WorldSession) -> None:
    payload = _reply("elder_qiu", {"type": "none"})
    result = handle_npc_reply(session, "guard_lin", payload)
    assert result["accepted"] is False
    assert result["errors"][0]["code"] == "NPC_REPLY_ID_MISMATCH"


def test_npc_instruction_rejected_by_world_rules(session: WorldSession) -> None:
    payload = _reply("elder_qiu", {"type": "world_edit", "instruction": "把重力改为 -500"})
    result = handle_npc_reply(session, "elder_qiu", payload)
    assert result["accepted"] is False
    assert any(issue["code"] == "SCHEMA" for issue in result["errors"])
