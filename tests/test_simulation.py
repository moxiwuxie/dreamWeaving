"""最小无头运行时：时钟、生理、天气可复现性与场景介质。"""

import json
import shutil
from pathlib import Path

import pytest

from dreamweave.__main__ import main
from dreamweave.scenes import scene_entry
from dreamweave.simulation import Simulation, weather_roll_probability
from dreamweave.survival import SurvivalPolicy, apply_survival, survival_sources
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _copy_demo(tmp_path: Path) -> Path:
    target = tmp_path / "world"
    shutil.copytree(DEMO, target)
    return target


def _add_scene(world: Path, scene_id: str, low: list[int], high: list[int]) -> None:
    path = world / "config" / "scenes.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["scenes"].append(scene_entry(scene_id, low, high))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_tick_advances_clock_and_drains_vitals():
    simulation = Simulation(WorldSession(DEMO))
    snapshot = simulation.tick(120, step=30)
    assert snapshot["clock"] == {"day": 1, "minute": 120, "time": "02:00"}
    assert snapshot["player"]["hunger"] < 1.0
    assert snapshot["player"]["thirst"] < 1.0


def test_same_seed_is_reproducible():
    left = Simulation(WorldSession(DEMO), seed=7).tick(1440, 60)
    right = Simulation(WorldSession(DEMO), seed=7).tick(1440, 60)
    assert left == right


def test_weather_probability_is_scaled_by_roll_interval():
    assert weather_roll_probability(0.3, 1440) == pytest.approx(0.3)
    assert weather_roll_probability(0.3, 360) == pytest.approx(1 - 0.7 ** 0.25)
    assert weather_roll_probability(0.0, 360) == 0.0
    assert weather_roll_probability(1.0, 10) == 1.0


def test_cold_scene_drives_core_temperature_down(tmp_path):
    world = _copy_demo(tmp_path)
    _add_scene(world, "glacier", [0, 64, 0], [63, 95, 63])
    simulation = Simulation(WorldSession(world), position_m=[10.0, 300.0, 10.0])
    assert simulation.scene is not None and simulation.scene["id"] == "glacier"

    snapshot = simulation.tick(600, step=60)
    assert snapshot["player"]["core_temperature_c"] < 36.8
    assert any(effect["id"] == "hypothermia" for effect in snapshot["effects"])


def test_underwater_scene_consumes_oxygen(tmp_path):
    world = _copy_demo(tmp_path)
    _add_scene(world, "ocean", [0, 64, 0], [63, 95, 63])
    simulation = Simulation(WorldSession(world), position_m=[10.0, 300.0, 10.0])
    assert simulation.scene is not None and simulation.scene["id"] == "ocean"

    snapshot = simulation.tick(3, step=1)
    assert snapshot["player"]["oxygen_seconds"] == 0
    assert snapshot["player"]["health"] < 100


def test_npcs_follow_daily_schedule():
    simulation = Simulation(WorldSession(DEMO))
    initial = {npc["npc_id"]: npc for npc in simulation.snapshot()["npcs"]}
    assert initial["guard_lin"]["activity"] == "patrol"
    assert initial["merchant_zhao"]["activity"] == "sleep"

    snapshot = simulation.tick(390, step=30)
    by_id = {npc["npc_id"]: npc for npc in snapshot["npcs"]}
    assert by_id["merchant_zhao"]["activity"] == "eat"
    assert by_id["merchant_zhao"]["location"] == "tavern"
    assert by_id["elder_qiu"]["scene_id"] == "city_core"
    assert any(event["type"] == "npc_activity" and "merchant_zhao" in event["value"] for event in simulation.events)
    assert any(event["type"] == "npc_move" and "elder_qiu" in event["value"] for event in simulation.events)


def test_storm_interrupts_outdoor_schedule_and_recovers():
    simulation = Simulation(WorldSession(DEMO), minute=600)
    simulation.weather = {"id": "test_storm", "kind": "storm", "intensity": 1.0}
    simulation._weather_until = simulation._absolute_minute + 10000

    sheltered = simulation.tick(10, step=10)
    by_id = {npc["npc_id"]: npc for npc in sheltered["npcs"]}
    assert by_id["merchant_zhao"]["activity"] == "shelter"
    assert by_id["merchant_zhao"]["status"] == "sheltered"
    assert by_id["merchant_zhao"]["location"] == "home"
    assert by_id["guard_lin"]["activity"] == "sleep"

    simulation.weather = None
    recovered = simulation.tick(10, step=10)
    by_id = {npc["npc_id"]: npc for npc in recovered["npcs"]}
    assert by_id["merchant_zhao"]["activity"] == "work"
    assert by_id["merchant_zhao"]["status"] == "scheduled"


def test_heatwave_pressures_plants_then_herbivores():
    simulation = Simulation(WorldSession(DEMO))
    simulation.weather = {"id": "test_heat", "kind": "heatwave", "intensity": 1.0}
    simulation._weather_until = simulation._absolute_minute + 10000

    snapshot = simulation.tick(1440, step=60)
    by_id = {item["id"]: item for item in snapshot["ecology"]}
    assert by_id["grass"]["population"] < 1.0
    assert by_id["rabbit"]["population"] < 1.0
    assert by_id["rabbit"]["population"] > by_id["grass"]["population"]


def test_cli_simulate_text_includes_npcs_and_ecology(capsys):
    assert main(["simulate", str(DEMO), "--minutes", "60", "--step", "60"]) == 0
    out = capsys.readouterr().out
    assert "NPC 3 人" in out
    assert "生态" in out


class _Stomach:
    hunger = 0.2
    thirst = 0.2


def test_survival_layer_consumes_available_resources():
    policy = SurvivalPolicy()
    stomach = _Stomach()
    uef = {"resources": {"water_availability": 0.9, "biomass": 0.8}, "society": {"built_density": 0.1}}

    status = apply_survival(stomach, uef, 1.0, policy)

    assert status == {"ate": True, "drank": True, "food": True, "water": True}
    assert stomach.thirst == pytest.approx(0.8)
    assert stomach.hunger == pytest.approx(0.5)


def test_survival_layer_respects_empty_scene_and_disabled_policy():
    stomach = _Stomach()
    empty = {"resources": {"water_availability": 0.1, "biomass": 0.1}, "society": {"built_density": 0.1}}
    status = apply_survival(stomach, empty, 1.0, SurvivalPolicy())
    assert status == {"ate": False, "drank": False, "food": False, "water": False}
    assert stomach.hunger == 0.2 and stomach.thirst == 0.2

    policy = SurvivalPolicy.from_module({"survival": {"auto_eat": False, "eat_threshold": 0.5}})
    assert policy.auto_eat is False
    assert policy.eat_threshold == 0.5
    assert survival_sources({"resources": {"water_availability": 0.5}}, policy)["water"] is True


def test_simulation_auto_eats_and_drinks_in_town():
    simulation = Simulation(WorldSession(DEMO), position_m=[32, 8, 32], seed=11)
    simulation.tick(1200, step=60)

    values = [event["value"] for event in simulation.events if event["type"] == "survival"]
    assert "饮水开始" in values
    assert "进食开始" in values
    assert simulation.player.thirst > 0.4
    assert simulation.player.hunger > 0.3
    assert simulation.snapshot()["survival"]["water"] is True
    assert simulation.snapshot()["survival"]["food"] is True


def test_state_round_trip_matches_uninterrupted_run():
    interrupted = Simulation(WorldSession(DEMO), seed=11)
    interrupted.tick(600, step=60)
    state = interrupted.to_state()

    resumed = Simulation.from_state(WorldSession(DEMO), state)
    resumed.tick(840, step=60)

    straight = Simulation(WorldSession(DEMO), seed=11)
    expected = straight.tick(1440, step=60)

    assert resumed.snapshot() == expected
    assert resumed.events == straight.events


def test_state_rejects_unknown_version():
    state = Simulation(WorldSession(DEMO)).to_state()
    state["state_version"] = 99
    with pytest.raises(ValueError, match="存档版本"):
        Simulation.from_state(WorldSession(DEMO), state)


def test_cli_simulate_state_round_trip(tmp_path, capsys):
    state_path = tmp_path / "sim_state.json"

    assert (
        main(
            [
                "simulate",
                str(DEMO),
                "--minutes",
                "120",
                "--step",
                "60",
                "--json",
                "--state-out",
                str(state_path),
            ]
        )
        == 0
    )
    assert state_path.is_file()
    assert json.loads(capsys.readouterr().out)["snapshot"]["clock"]["minute"] == 120

    assert (
        main(
            [
                "simulate",
                str(DEMO),
                "--minutes",
                "120",
                "--step",
                "60",
                "--json",
                "--state-in",
                str(state_path),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["snapshot"]["clock"] == {"day": 1, "minute": 240, "time": "04:00"}


def test_cli_simulate_rejects_broken_state(tmp_path, capsys):
    state_path = tmp_path / "broken.json"
    state_path.write_text("{not json", encoding="utf-8")

    assert main(["simulate", str(DEMO), "--state-in", str(state_path)]) == 2
    assert "无法恢复模拟状态" in capsys.readouterr().err


def test_cli_simulate_json(capsys):
    assert main(["simulate", str(DEMO), "--minutes", "120", "--step", "60", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["snapshot"]["clock"]["minute"] == 120
    assert isinstance(payload["events"], list)
