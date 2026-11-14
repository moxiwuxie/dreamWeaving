"""任务操作剧情：任务列表、旅途时长、编年史节拍与矛盾推进。"""

from pathlib import Path

from dreamweave import build_story_context
from dreamweave.quests import (
    build_quest_beat,
    quest_list,
    quest_operations,
    travel_minutes,
)
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _session() -> WorldSession:
    return WorldSession(DEMO)


def _quest(quest_id: str) -> dict:
    return next(item for item in _session().modules["narrative"]["quests"] if item["id"] == quest_id)


def test_quest_list_decorates_scene_action_and_completion():
    quests = quest_list(_session(), completed={"inspect_granary"})
    by_id = {item["id"]: item for item in quests}

    assert by_id["draw_river_water"]["scene_name"] == "城市中心"
    assert by_id["draw_river_water"]["action_label"] == "劳作"
    assert by_id["draw_river_water"]["completed"] is False
    assert by_id["inspect_granary"]["completed"] is True


def test_travel_minutes_uses_walk_speed_and_zero_for_same_point():
    assert travel_minutes(None, [0, 0, 0]) == 0
    assert travel_minutes([10, 0, 0], [10.5, 0, 0]) == 0
    assert travel_minutes([0, 0, 0], [84, 0, 0]) == 1
    assert travel_minutes([0, 0, 0], [168, 0, 0]) == 2


def test_build_quest_beat_mentions_scene_action_weather_and_temperature():
    entry = build_quest_beat(
        _session(),
        _quest("draw_river_water"),
        minute=1440 + 90,
        weather={"kind": "heatwave", "intensity": 0.3},
        ambient_temperature_c=31.5,
    )

    assert entry["id"] == "quest_draw_river_water"
    assert entry["day"] == 2
    assert entry["tension_ref"] == "water_dispute"
    assert "为旱地取水" in entry["summary"]
    assert "城市中心" in entry["summary"]
    assert "劳作" in entry["summary"]
    assert "热浪" in entry["summary"]
    assert "31.5" in entry["summary"]
    assert entry["participants"] == ["river_kingdom", "hill_clans"]


def test_build_quest_beat_handles_calm_weather():
    entry = build_quest_beat(_session(), _quest("inspect_granary"), minute=0)
    assert "天气平稳" in entry["summary"]


def test_quest_operations_append_chronicle_and_raise_tension():
    session = _session()
    quest = _quest("draw_river_water")
    entry = build_quest_beat(session, quest, minute=60)

    operations = quest_operations(session, quest, entry)

    assert operations[0] == {"op": "append", "path": "narrative.chronicle", "value": entry}
    assert operations[1]["path"] == "narrative.tensions.0.intensity"
    assert operations[1]["value"] == 0.7


def test_quest_operations_escalate_stage_when_threshold_crossed():
    session = _session()
    quest = _quest("inspect_granary")
    entry = build_quest_beat(session, quest, minute=60)

    operations = quest_operations(session, quest, entry)

    assert operations[1]["value"] == 0.45
    assert operations[2] == {"op": "set", "path": "narrative.tensions.1.stage", "value": "escalating"}


def test_quest_operations_without_tension_only_appends_chronicle():
    session = _session()
    quest = {**_quest("draw_river_water"), "tension_id": None}
    operations = quest_operations(session, quest, build_quest_beat(session, quest, minute=60))
    assert [item["op"] for item in operations] == ["append"]


def test_story_context_injects_recent_player_actions():
    actions = [
        {
            "day": 1,
            "time": "06:00",
            "title": "为旱地取水",
            "scene_id": "city_core",
            "duration_min": 60,
        }
    ]
    built = build_story_context(_session(), day=2, actions=actions)

    assert built["context"]["player_actions"] == actions
    assert "玩家近期操作" in built["prompt"]
    assert "为旱地取水" in built["prompt"]
    assert "第 1 天 06:00" in built["prompt"]
