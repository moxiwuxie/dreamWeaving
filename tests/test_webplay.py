"""本地网页控制台：HTTP 路由、推进与重置。"""

import json
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from dreamweave.webplay import Playground, create_server

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _copy_demo(tmp_path: Path) -> Path:
    target = tmp_path / "world"
    shutil.copytree(DEMO, target)
    return target


def _request(server, path: str, method: str = "GET", payload: dict | None = None):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request) as response:
        return response.status, response.read().decode("utf-8")


@pytest.fixture
def server(tmp_path):
    playground = Playground(str(_copy_demo(tmp_path)))
    httpd = create_server(playground, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, playground
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_playground_serves_3d_game_and_console(server):
    httpd, _ = server
    status, body = _request(httpd, "/")
    assert status == 200
    assert "gl-canvas" in body
    assert "/static/app.js" in body
    assert 'type="module"' in body

    status, body = _request(httpd, "/static/app.js")
    assert status == 200
    assert "./hud.js" in body

    status, body = _request(httpd, "/static/hud.js")
    assert status == 200
    assert "/api/view" in body

    status, body = _request(httpd, "/console")
    assert status == 200
    assert "本地控制台" in body
    assert "/api/tick" in body


def test_playground_view_projects_scenes_and_npcs(server):
    httpd, playground = server
    status, body = _request(httpd, "/api/view")
    data = json.loads(body)

    assert status == 200
    assert data["world"]["id"] == "earth_demo"
    assert data["time"]["day"] == 1
    assert data["weather"]["kind"] in ("clear", "rain", "snow", "storm", "fog")

    scenes = {item["id"]: item for item in data["scenes"]}
    assert "city_core" in scenes
    city = scenes["city_core"]
    assert city["bounds_m"]["max"][0] > city["bounds_m"]["min"][0]
    assert city["ground_color"].startswith("#")
    assert city["center_m"][0] == pytest.approx(
        (city["bounds_m"]["min"][0] + city["bounds_m"]["max"][0]) / 2, abs=0.01
    )

    assert data["player"]["scene_id"] == "city_core"
    assert data["player"]["feet_m"][0] == playground.spawn[0]
    assert data["npcs"]
    for npc in data["npcs"]:
        assert npc["id"] and npc["position_m"]
        assert npc["scene_id"] in scenes
        low = scenes[npc["scene_id"]]["bounds_m"]["min"]
        high = scenes[npc["scene_id"]]["bounds_m"]["max"]
        assert low[0] <= npc["position_m"][0] <= high[0]
        assert low[2] <= npc["position_m"][2] <= high[2]

    assert data["quests"]
    assert data["events"] == []
    assert data["recap"] is None

    # 可交互物件随场景下发，并与场景内静态几何保持一致。
    assert data["props"]
    assert data["interactions"] == []
    city_props = [item for item in data["props"] if item["scene_id"] == "city_core"]
    assert city_props
    for prop in city_props:
        assert prop["id"].startswith("city_core:")
        assert prop["name"]
        assert len(prop["position_m"]) == 3
        assert prop["verbs"] == ["inspect", "use", "harvest"]
    assert city["props"] == city_props


def test_playground_interact_npc_changes_trust_and_logs(server):
    httpd, playground = server
    npc = playground.view()["npcs"][0]
    payload = {
        "target": "npc",
        "id": npc["id"],
        "action": "talk",
        "position_m": npc["position_m"],
    }
    status, body = _request(httpd, "/api/interact", "POST", payload)
    data = json.loads(body)

    assert status == 200
    assert data["accepted"] is True
    assert data["speech"]
    assert data["trust"] == pytest.approx(0.54)
    assert data["trust_label"] == "平淡"
    assert data["state"]["interactions"][-1]["action"] == "交谈"
    assert data["state"]["sensory"]["last"]["deliveries"]

    payload["action"] = "hit"
    status, body = _request(httpd, "/api/interact", "POST", payload)
    data = json.loads(body)
    assert status == 200
    assert data["trust"] == pytest.approx(0.29)
    assert data["trust_label"] == "戒备"


def test_playground_interact_prop_applies_effects(server):
    httpd, playground = server
    well = next(item for item in playground.view()["props"] if item["kind"] == "well")
    playground.simulation.player.thirst = 0.3
    before = playground.simulation.player.thirst
    status, body = _request(
        httpd,
        "/api/interact",
        "POST",
        {
            "target": "prop",
            "id": well["id"],
            "action": "use",
            "position_m": well["position_m"],
        },
    )
    data = json.loads(body)

    assert status == 200
    assert data["effects"]["thirst"] == pytest.approx(0.4)
    assert playground.simulation.player.thirst > before
    assert data["state"]["interactions"][-1]["name"] == well["name"]


def test_playground_interact_rejects_bad_target_and_distance(server):
    httpd, playground = server
    npc = playground.view()["npcs"][0]

    with pytest.raises(urllib.error.HTTPError) as far:
        _request(
            httpd,
            "/api/interact",
            "POST",
            {
                "target": "npc",
                "id": npc["id"],
                "action": "talk",
                "position_m": [npc["position_m"][0] + 40, npc["position_m"][1], npc["position_m"][2]],
            },
        )
    assert far.value.code == 400

    with pytest.raises(urllib.error.HTTPError) as unknown:
        _request(
            httpd,
            "/api/interact",
            "POST",
            {"target": "npc", "id": "no_such_npc", "action": "talk"},
        )
    assert unknown.value.code == 400

    with pytest.raises(urllib.error.HTTPError) as missing:
        _request(httpd, "/api/interact", "POST", {"target": "npc"})
    assert missing.value.code == 400


def test_playground_notice_writing_and_reading(server):
    httpd, playground = server
    notice = next(item for item in playground.view()["props"] if item["kind"] == "notice")
    payload = {
        "target": "prop",
        "id": notice["id"],
        "action": "use",
        "position_m": notice["position_m"],
        "text": "今晚广场见",
    }
    status, body = _request(httpd, "/api/interact", "POST", payload)
    data = json.loads(body)
    assert status == 200
    assert "今晚广场见" in data["message"]
    assert data["state"]["interactions"][-1]["action"] == "使用"

    payload = {
        "target": "prop",
        "id": notice["id"],
        "action": "inspect",
        "position_m": notice["position_m"],
    }
    status, body = _request(httpd, "/api/interact", "POST", payload)
    data = json.loads(body)
    assert status == 200
    assert "今晚广场见" in data["message"]


def test_playground_harvest_carries_and_drops_prop(server):
    httpd, playground = server
    crate = next(item for item in playground.view()["props"] if item["kind"] == "crate")
    payload = {
        "target": "prop",
        "id": crate["id"],
        "action": "harvest",
        "position_m": crate["position_m"],
    }
    status, body = _request(httpd, "/api/interact", "POST", payload)
    data = json.loads(body)
    assert status == 200
    assert data["carried"]["kind"] == "crate"
    assert data["carried"]["name"] == crate["name"]
    assert playground.view()["carried"]["id"] == crate["id"]

    status, body = _request(
        httpd,
        "/api/interact",
        "POST",
        {"target": "carry", "id": crate["id"], "action": "drop"},
    )
    data = json.loads(body)
    assert status == 200
    assert data["carried"] is None
    assert playground.view()["carried"] is None
    dropped = next(item for item in playground.view()["props"] if item["id"] == crate["id"])
    spot = playground.simulation.position_m
    assert abs(dropped["position_m"][0] - spot[0]) <= 2.5
    assert abs(dropped["position_m"][2] - spot[2]) <= 2.5


def test_playground_drop_without_carry_is_rejected(server):
    httpd, _ = server
    with pytest.raises(urllib.error.HTTPError) as missing:
        _request(httpd, "/api/interact", "POST", {"target": "carry", "id": "held", "action": "drop"})
    assert missing.value.code == 400


def test_playground_persists_carried_and_prop_positions(tmp_path):
    world = _copy_demo(tmp_path)
    first = Playground(str(world))
    assert first.state_path.parent == tmp_path / ".dreamweave" / "runtime"

    crate = next(item for item in first.view()["props"] if item["kind"] == "crate")
    first.interact("prop", crate["id"], "harvest", position_m=crate["position_m"])
    assert first.state_path.is_file()

    holding = Playground(str(world))
    assert holding.carried["id"] == crate["id"]
    assert holding.interactions[-1]["action"] == "采集"

    first.interact("carry", crate["id"], "drop", position_m=first.simulation.position_m)
    dropped = next(item for item in first.view()["props"] if item["id"] == crate["id"])

    restored = Playground(str(world))
    assert restored.carried is None
    spot = next(item for item in restored.props if item["id"] == crate["id"])["position_m"]
    assert spot == dropped["position_m"]


def test_playground_persists_npc_trust_and_notes_then_reset(tmp_path):
    world = _copy_demo(tmp_path)
    first = Playground(str(world))
    npc = first.view()["npcs"][0]
    first.interact("npc", npc["id"], "talk", position_m=npc["position_m"])
    notice = next(item for item in first.view()["props"] if item["kind"] == "notice")
    first.interact(
        "prop", notice["id"], "use",
        position_m=notice["position_m"], text="今晚广场见",
    )

    restored = Playground(str(world))
    assert restored.trust[npc["id"]] == pytest.approx(0.54)
    assert restored.talk_counts[npc["id"]] == 1
    assert restored.prop_notes[notice["id"]] == "今晚广场见"
    assert restored.interactions

    restored.reset()
    assert not restored.state_path.exists()
    fresh = Playground(str(world))
    assert fresh.trust == {}
    assert fresh.prop_notes == {}
    assert fresh.interactions == []


def test_playground_state_uses_scene_spawn(server):
    httpd, playground = server
    status, body = _request(httpd, "/api/state")
    data = json.loads(body)

    assert status == 200
    assert data["world"]["id"] == "earth_demo"
    assert data["world"]["scene"]["id"] == "city_core"
    assert data["world"]["position_m"] == playground.spawn
    assert data["snapshot"]["clock"] == {"day": 1, "minute": 0, "time": "00:00"}
    assert data["snapshot"]["npcs"]


def test_playground_state_exposes_model_physics_and_initial(server):
    httpd, playground = server
    status, body = _request(httpd, "/api/state")
    data = json.loads(body)

    assert status == 200
    assert data["model"]["template"] == "earth"
    assert any(item["id"] == "narrative" for item in data["model"]["modules"])
    assert data["model"]["npc_count"] == len(
        playground.session.modules["npc_agent"]["npc_roster"]
    )
    assert {item["id"] for item in data["model"]["nations"]}
    assert data["physics"]["gravity"]
    assert data["initial"]["spawn"] == playground.spawn
    assert data["initial"]["seed"] == playground.seed
    assert data["quests"]
    assert data["actions"] == []


def test_playground_state_exposes_sensory_bus(server):
    httpd, _ = server
    status, body = _request(httpd, "/api/state")
    data = json.loads(body)

    assert status == 200
    assert data["sensory"]["config"]["sync_window_ms"] == 45
    assert data["sensory"]["config"]["channels"]["haptic"]["sync_mode"] == "hard"
    assert data["sensory"]["last"] is None


def test_sensory_preset_routes_and_syncs(server):
    httpd, _ = server
    status, body = _request(httpd, "/api/sensory", "POST", {"preset": "impact"})
    data = json.loads(body)

    assert status == 200
    assert data["accepted"] is True
    plan = data["state"]["sensory"]["last"]
    assert plan["diagnostics"]["deliveries"] == 4
    assert {delivery["channel"] for delivery in plan["deliveries"]} == {"visual", "audio", "haptic"}
    assert len({delivery["at_ms"] for delivery in plan["deliveries"]}) == 1
    assert plan["events"][0]["sync"]["status"] == "aligned"


def test_sensory_status_maps_physiology(server):
    httpd, playground = server
    status, body = _request(httpd, "/api/sensory", "POST", {"status": True})
    data = json.loads(body)

    assert status == 200
    plan = data["state"]["sensory"]["last"]
    effects = playground.session.modules["player_body"]["status_effects"]
    assert len(plan["events"]) == len(effects)
    assert {event["kind"] for event in plan["events"]} == {"status"}
    assert plan["deliveries"]


def test_sensory_unknown_preset_is_rejected(server):
    httpd, _ = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _request(httpd, "/api/sensory", "POST", {"preset": "teleport"})
    assert exc.value.code == 400


def test_playground_act_completes_quest_and_writes_chronicle(server):
    httpd, playground = server
    status, body = _request(httpd, "/api/act", "POST", {"quest_id": "draw_river_water"})
    data = json.loads(body)

    assert status == 200
    assert data["accepted"] is True
    assert data["story"]["id"] == "quest_draw_river_water"
    assert data["state"]["snapshot"]["clock"]["time"] == "01:00"
    assert playground.simulation.absolute_minute == 60
    assert data["state"]["narrative"]["chronicle"][-1]["id"] == "quest_draw_river_water"
    assert data["state"]["actions"][-1]["quest_id"] == "draw_river_water"
    quests = {item["id"]: item for item in data["state"]["quests"]}
    assert quests["draw_river_water"]["completed"] is True
    assert quests["inspect_granary"]["completed"] is False

    water = next(
        item for item in data["state"]["narrative"]["tensions"] if item["id"] == "water_dispute"
    )
    assert water["intensity"] == 0.7


def test_playground_act_rejects_repeat_and_unknown_quest(server):
    httpd, _ = server
    _request(httpd, "/api/act", "POST", {"quest_id": "draw_river_water"})

    with pytest.raises(urllib.error.HTTPError) as repeat:
        _request(httpd, "/api/act", "POST", {"quest_id": "draw_river_water"})
    assert repeat.value.code == 400

    with pytest.raises(urllib.error.HTTPError) as unknown:
        _request(httpd, "/api/act", "POST", {"quest_id": "no_such_quest"})
    assert unknown.value.code == 400

    with pytest.raises(urllib.error.HTTPError) as missing:
        _request(httpd, "/api/act", "POST", {})
    assert missing.value.code == 400


def test_playground_timeflow_returns_memory_recap(server):
    httpd, playground = server
    status, body = _request(httpd, "/api/timeflow", "POST", {"days": 2, "step": 60})
    data = json.loads(body)

    assert status == 200
    assert data["accepted"] is True
    assert data["recap"]["beats"]
    assert data["state"]["snapshot"]["clock"]["day"] == 3
    assert data["state"]["timeflow"]["recap"]["summary"] == data["recap"]["summary"]
    assert playground.simulation.absolute_minute == 2880


def test_playground_reset_clears_quest_progress(server):
    httpd, playground = server
    _request(httpd, "/api/act", "POST", {"quest_id": "draw_river_water"})
    status, body = _request(httpd, "/api/reset", "POST", {})
    data = json.loads(body)

    assert status == 200
    assert data["state"]["actions"] == []
    assert all(item["completed"] is False for item in data["state"]["quests"])
    assert playground.simulation.absolute_minute == 0


def test_playground_tick_advances_clock(server):
    httpd, playground = server
    status, body = _request(httpd, "/api/tick", "POST", {"minutes": 720, "step": 60})
    data = json.loads(body)

    assert status == 200
    assert data["accepted"] is True
    assert data["state"]["snapshot"]["clock"]["time"] == "12:00"
    assert playground.simulation.absolute_minute == 720
    assert data["events"]


def test_playground_advance_writes_chronicle(server):
    httpd, playground = server
    status, body = _request(httpd, "/api/advance", "POST", {"days": 1})
    data = json.loads(body)

    assert status == 200
    assert data["accepted"] is True, data["errors"]
    assert data["state"]["snapshot"]["clock"]["day"] == 2
    assert data["chronicle"]
    assert data["state"]["narrative"]["chronicle"]
    assert playground.simulation.absolute_minute == 1440


def test_playground_reset_restores_start(server):
    httpd, playground = server
    _request(httpd, "/api/tick", "POST", {"minutes": 600, "step": 60})
    status, body = _request(httpd, "/api/reset", "POST", {})
    data = json.loads(body)

    assert status == 200
    assert data["state"]["snapshot"]["clock"] == {"day": 1, "minute": 0, "time": "00:00"}
    assert playground.simulation.absolute_minute == 0
