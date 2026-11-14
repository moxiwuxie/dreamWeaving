"""感知时间层：事件采样、主观时长与记忆回放。"""

from pathlib import Path

import pytest

from dreamweave.simulation import Simulation
from dreamweave.timeflow import (
    MAX_BEATS,
    advance_perceptual,
    build_recap,
    sample_beats,
    subjective_ratio,
)
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _events() -> list[dict]:
    return [
        {"minute": 10, "type": "weather", "value": "rain"},
        {"minute": 20, "type": "npc_activity", "value": "merchant_zhao work"},
        {"minute": 30, "type": "npc_activity", "value": "merchant_zhao shop"},
        {"minute": 40, "type": "unknown", "value": "x"},
    ]


def test_sample_beats_keeps_order_dedupes_and_labels():
    beats = sample_beats(_events())

    assert [beat["minute"] for beat in beats] == [10, 20, 40]
    assert beats[0]["label"] == "天候"
    assert beats[0]["day"] == 1
    assert beats[1]["value"] == "merchant_zhao work"
    assert beats[2]["label"] == "unknown"


def test_sample_beats_caps_by_salience_then_time_order():
    events = [
        {"minute": index, "type": "npc_activity", "value": f"npc_{index} work"}
        for index in range(MAX_BEATS + 6)
    ]
    beats = sample_beats(events)

    assert len(beats) == MAX_BEATS
    assert [beat["minute"] for beat in beats] == sorted(beat["minute"] for beat in beats)


def test_subjective_ratio_is_bounded_and_monotonic():
    assert subjective_ratio(0) == 0.7
    assert subjective_ratio(5) > subjective_ratio(1)
    assert subjective_ratio(1000) == 3.0
    assert subjective_ratio(-5) == 0.7


def test_build_recap_summarizes_compressed_span():
    recap = build_recap(_events(), start_minute=0, end_minute=1440 * 2)

    assert recap["days"] == 2
    assert recap["from_day"] == 1
    assert recap["to_day"] == 3
    assert recap["event_count"] == 4
    assert len(recap["beats"]) == 3
    assert recap["density"] == 1.5
    assert recap["subjective_ratio"] == 0.88
    assert recap["subjective_days"] == 1.76
    assert "压缩 2.00 天" in recap["summary"]
    assert "天候 1" in recap["summary"]


def test_advance_perceptual_ticks_simulation_and_replays_memory():
    session = WorldSession(DEMO)
    simulation = Simulation(session, position_m=session.scene_spawn(), seed=7)

    recap = advance_perceptual(simulation, 1440 * 2, step=60)

    assert simulation.absolute_minute == 2880
    assert recap["to_day"] == 3
    assert recap["beats"]
    assert recap["summary"]
    assert 0.5 <= recap["subjective_ratio"] <= 3.0


def test_advance_perceptual_rejects_non_positive_minutes():
    session = WorldSession(DEMO)
    simulation = Simulation(session, position_m=session.scene_spawn(), seed=7)
    with pytest.raises(ValueError):
        advance_perceptual(simulation, 0)
