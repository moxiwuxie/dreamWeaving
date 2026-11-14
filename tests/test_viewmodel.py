"""视图模型：结构、确定性、场景几何、时钟与天气契约。"""

import json
from pathlib import Path

from dreamweave.simulation import Simulation
from dreamweave.viewmodel import build_view
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _session() -> WorldSession:
    return WorldSession(DEMO)


def _simulation(session: WorldSession, *, day: int = 1, minute: int = 0) -> Simulation:
    return Simulation(
        session,
        day=day,
        minute=minute,
        position_m=session.scene_spawn(),
        seed=7,
    )


def test_view_has_expected_structure_and_scene_geometry():
    session = _session()
    simulation = _simulation(session)
    view = build_view(session, simulation)

    assert set(view) == {
        "world",
        "time",
        "weather",
        "player",
        "camera",
        "scenes",
        "npcs",
        "quests",
        "actions",
        "tensions",
        "chronicle",
        "recap",
        "events",
    }
    assert view["world"] == {
        "id": "earth_demo",
        "name": "地球基准演示世界",
        "template": "earth",
        "seed": 7,
    }
    assert len(view["scenes"]) == 5
    for scene in view["scenes"]:
        assert all(component > 0 for component in scene["size_m"])
        assert len(scene["center_m"]) == 3
        assert scene["ground_color"].startswith("#") and len(scene["ground_color"]) == 7
        assert scene["ground_color"] == scene["ground_color"].lower()
    assert len(view["npcs"]) == len(simulation.npcs)
    assert view["actions"] == []
    assert view["recap"] is None


def test_view_is_deterministic_across_calls():
    session = _session()
    simulation = _simulation(session)

    first = build_view(session, simulation, seed=7)
    second = build_view(session, simulation, seed=7)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_npc_positions_stay_inside_their_scene_bounds():
    session = _session()
    view = build_view(session, _simulation(session))
    scenes = {scene["id"]: scene for scene in view["scenes"]}

    for npc in view["npcs"]:
        assert npc["scene_id"] in scenes
        bounds = scenes[npc["scene_id"]]["bounds_m"]
        x, y, z = npc["position_m"]
        assert bounds["min"][0] <= x <= bounds["max"][0]
        assert bounds["min"][2] <= z <= bounds["max"][2]
        assert y == bounds["min"][1]


def test_daylight_and_clock_follow_sun_curve():
    for minute, expected in ((720, 1.0), (0, 0.0), (1260, 0.0)):
        session = _session()
        view = build_view(session, _simulation(session, minute=minute))

        assert abs(view["time"]["daylight"] - expected) < 1e-6
        assert view["time"]["clock"] == f"{minute // 60:02d}:{minute % 60:02d}"
        assert view["time"]["day"] == 1
        assert view["time"]["minute"] == minute


def test_completed_quests_are_marked():
    session = _session()
    view = build_view(
        session,
        _simulation(session),
        completed={"draw_river_water"},
    )
    quests = {item["id"]: item for item in view["quests"]}

    assert quests["draw_river_water"]["completed"] is True
    assert quests["inspect_granary"]["completed"] is False


def test_player_feet_sit_on_scene_floor():
    session = _session()
    view = build_view(session, _simulation(session))
    scene = next(
        item for item in view["scenes"] if item["id"] == view["player"]["scene_id"]
    )

    assert view["player"]["scene_id"] == "city_core"
    assert view["player"]["feet_m"][1] == scene["bounds_m"]["min"][1]


def test_clear_weather_uses_defaults():
    session = _session()
    view = build_view(session, _simulation(session))

    assert view["weather"]["kind"] == "clear"
    assert view["weather"]["label"] == "晴"
    assert view["weather"]["wind"] == [0.0, 0.0, 0.0]
