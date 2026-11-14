"""M011 统一感官事件总线：路由、同步、焦点降级与预算裁剪。"""

import json
import shutil
from pathlib import Path

import pytest

from dreamweave import build_perception, build_sensory_plan, status_effects_as_events, validate_world
from dreamweave.__main__ import main
from dreamweave.sensory_bus import SensoryEventBus, default_bus_config
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _event(event_id: str = "evt_1", **overrides) -> dict:
    event = {
        "schema_version": "0.1.0",
        "id": event_id,
        "kind": "impact",
        "t_ms": 0,
        "intensity": 0.8,
        "channels": {
            "visual": {"vfx": "vfx_test"},
            "audio": {"cue": "sfx_test"},
            "haptic": {"event": "haptic_test"},
        },
    }
    event.update(overrides)
    return event


def test_hard_channels_arrive_at_the_same_time():
    result = SensoryEventBus().publish(_event())

    arrivals = {delivery["channel"]: delivery["at_ms"] for delivery in result["deliveries"]}
    assert arrivals["visual"] == arrivals["audio"] == arrivals["haptic"] == 28
    plan = result["events"][0]
    assert plan["sync"]["status"] == "aligned"
    assert plan["sync"]["skew_ms"] == 18
    assert result["diagnostics"]["sync_violations"] == 0


def test_psychophysics_uses_stevens_curve():
    config = default_bus_config()
    config["channels"]["haptic"]["devices"] = [
        {"id": "haptic_probe", "enabled": True, "latency_ms": 10, "capabilities": []}
    ]
    event = _event(channels={"haptic": {"event": "haptic_test"}}, intensity=0.25)

    result = SensoryEventBus(config).publish(event)

    delivery = result["deliveries"][0]
    assert delivery["perceived_intensity"] == pytest.approx(0.25**0.75, abs=1e-4)


def test_off_focus_event_is_attenuated():
    result = SensoryEventBus().publish(
        _event(channels={"haptic": {"event": "haptic_test"}}, intensity=0.4, focus=False)
    )

    delivery = result["deliveries"][0]
    assert delivery["focus_gain"] == 0.35
    assert delivery["requested_intensity"] == pytest.approx(0.14)


def test_off_focus_event_below_threshold_is_ignored():
    result = SensoryEventBus().publish(
        _event(channels={"haptic": {"event": "haptic_test"}}, intensity=0.02, focus=False)
    )

    assert result["deliveries"] == []
    assert any(drop["reason"] == "focus_culled" for drop in result["events"][0]["dropped"])
    assert result["diagnostics"]["focus_culled"] == 1


def test_slow_channel_is_dropped_outside_sync_window():
    config = default_bus_config()
    config["sync_window_ms"] = 20
    config["channels"]["haptic"]["devices"] = [
        {"id": "slow_haptic", "enabled": True, "latency_ms": 40, "capabilities": ["wideband"]}
    ]

    result = SensoryEventBus(config).publish(_event())

    assert {delivery["channel"] for delivery in result["deliveries"]} == {"visual", "audio"}
    plan = result["events"][0]
    assert plan["sync"]["status"] == "degraded"
    assert any(drop["channel"] == "haptic" and drop["reason"] == "sync_window" for drop in plan["dropped"])
    assert result["diagnostics"]["sync_violations"] == 1


def test_scent_is_delivered_on_soft_channel():
    event = _event(
        channels={"visual": {"vfx": "vfx_test"}, "scent": {"scent_id": "scent_bread"}}
    )

    result = SensoryEventBus().publish(event)

    plan = result["events"][0]
    assert plan["sync"]["group"] == "mixed"
    scent = [delivery for delivery in result["deliveries"] if delivery["channel"] == "scent"]
    assert len(scent) == 1
    assert scent[0]["at_ms"] == 60
    assert scent[0]["capabilities"] == ["soft_hint"]


def test_channel_budget_drops_excess_within_one_second():
    config = default_bus_config()
    config["channels"]["haptic"]["enabled"] = False
    config["budget"]["per_channel_events_per_sec"]["audio"] = 2
    events = [
        _event(f"step_{index}", kind="step", t_ms=index * 10, channels={"audio": {"cue": "sfx_step"}})
        for index in range(3)
    ]

    result = SensoryEventBus(config).plan(events)

    assert len([delivery for delivery in result["deliveries"] if delivery["channel"] == "audio"]) == 2
    assert result["diagnostics"]["budget_culled"] == 1
    assert any(drop["reason"] == "channel_budget" for drop in result["dropped"])


def test_channel_budget_counts_events_not_devices():
    config = default_bus_config()
    config["channels"]["haptic"]["devices"] = [
        {"id": "haptic_a", "enabled": True, "latency_ms": 10, "capabilities": ["wideband"]},
        {"id": "haptic_b", "enabled": True, "latency_ms": 12, "capabilities": ["wideband"]},
    ]
    config["budget"]["per_channel_events_per_sec"]["haptic"] = 1
    events = [
        _event("haptic_1", t_ms=0, channels={"haptic": {"event": "haptic_test"}}),
        _event("haptic_2", t_ms=10, channels={"haptic": {"event": "haptic_test"}}),
    ]

    result = SensoryEventBus(config).plan(events)

    assert {delivery["event_id"] for delivery in result["deliveries"]} == {"haptic_1"}
    assert len([delivery for delivery in result["deliveries"] if delivery["channel"] == "haptic"]) == 2
    assert sum(1 for drop in result["dropped"] if drop["reason"] == "channel_budget") == 2


def test_frame_budget_keeps_high_priority_event():
    config = default_bus_config()
    config["budget"]["max_events_per_tick"] = 1

    result = SensoryEventBus(config).plan(
        [
            _event("low_priority", t_ms=0, priority=0.2),
            _event("high_priority", t_ms=1, priority=0.9),
        ]
    )

    assert {delivery["event_id"] for delivery in result["deliveries"]} == {"high_priority"}
    assert any(drop["reason"] == "frame_budget" for drop in result["dropped"])


def test_invalid_event_is_rejected():
    result = SensoryEventBus().publish(
        {
            "schema_version": "0.1.0",
            "id": "bad_intensity",
            "kind": "impact",
            "t_ms": 0,
            "intensity": 1.5,
            "channels": {"visual": {"vfx": "vfx_test"}},
        }
    )

    assert result["ok"] is False
    assert result["rejected"][0]["event_id"] == "bad_intensity"
    assert any(item["path"] == "intensity" for item in result["rejected"][0]["errors"])


def test_status_effects_become_sensory_events():
    session = WorldSession(DEMO)
    effects = session.modules["player_body"]["status_effects"]

    events = status_effects_as_events(effects)
    result = build_sensory_plan(session, events)

    assert len(events) == len(effects)
    assert result["ok"] is True
    assert result["deliveries"]
    assert {delivery["kind"] for delivery in result["deliveries"]} == {"status"}


def test_perception_exposes_bus_contract():
    frame = build_perception(WorldSession(DEMO), position_m=[0, 0, 0])

    bus = frame["sensory_bus"]
    assert bus["sync_window_ms"] == 45
    assert bus["soft_sync_window_ms"] == 400
    assert bus["channels"]["haptic"]["sync_mode"] == "hard"
    assert bus["channels"]["scent"]["sync_mode"] == "soft"


def test_demo_world_validates_bus_without_warnings():
    report = validate_world(DEMO)

    assert report.ok
    assert "sensory_bus" in report.loaded_modules
    assert not [issue for issue in report.warnings if issue.code.startswith("BUS_")]


def test_validator_warns_when_hard_channel_is_soft(tmp_path):
    target = tmp_path / "world"
    shutil.copytree(DEMO, target)
    path = target / "config" / "sensory_bus.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["channels"]["audio"]["sync_mode"] = "soft"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    report = validate_world(target)

    assert any(issue.code == "BUS_HARD_CHANNEL_SOFT" for issue in report.warnings)


def test_cli_sensory_show_and_plan(capsys):
    assert main(["sensory", str(DEMO), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["channels"]["haptic"]["devices"]

    code = main(["sensory", str(DEMO), "--events", json.dumps(_event()), "--json"])
    assert code == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["diagnostics"]["deliveries"] == 4
    assert plan["events"][0]["event_id"] == "evt_1"
