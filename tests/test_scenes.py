"""场景库与统一环境场：目录合法性、推导规则与场景层校验。"""

import json
import shutil
from pathlib import Path

import pytest

from dreamweave import build_npc_context, create_world, validate_world
from dreamweave.__main__ import main
from dreamweave.scenes import (
    CATEGORIES,
    SCENES,
    derive_physics,
    derive_sensory,
    get_scene,
    list_scenes,
    scene_entry,
    unified_model,
)
from dreamweave.schemas import SchemaSet
from dreamweave.world import WorldError, WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _copy_demo(tmp_path: Path) -> Path:
    target = tmp_path / "world"
    shutil.copytree(DEMO, target)
    return target


def _edit_scenes(world: Path, mutate) -> None:
    path = world / "config" / "scenes.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_catalog_covers_all_categories():
    assert len(SCENES) >= 50
    listed = {entry["category"] for entry in list_scenes()}
    assert listed == set(CATEGORIES)
    for required in ("city_core", "rural_village", "mountain_range", "desert", "ocean", "island",
                     "abyssal_plain", "deep_space", "lunar_surface"):
        assert get_scene(required)["id"] == required


def test_every_catalog_scene_matches_schema():
    schemas = SchemaSet()
    for scene in SCENES:
        entry = scene_entry(scene["id"], [0, 0, 0], [1, 1, 1])
        errors = schemas.iter_errors({"schema_version": "0.1.0", "scenes": [entry]}, "world_scenes.schema.json")
        assert not errors, (scene["id"], errors)


def test_derive_physics_from_medium():
    ocean = derive_physics("ocean")
    assert ocean["air_density"] == 1025.0
    assert ocean["speed_of_sound"] == 1530.0
    vacuum = derive_physics("deep_space")
    assert vacuum["speed_of_sound"] >= 0.001
    assert vacuum["thermal"]["ambient_temperature_c"] == -270


def test_derive_sensory_follows_medium_and_enclosure():
    assert derive_sensory("ocean")["reverb_preset"] == "underwater"
    assert derive_sensory("karst_cave")["reverb_preset"] == "cave"
    assert derive_sensory("deep_space")["reverb_preset"] == "custom"
    assert derive_sensory("karst_cave")["occlusion_attenuation"] > derive_sensory("plains")["occlusion_attenuation"]


def test_unified_model_exposes_six_axes_and_rules():
    model = unified_model()
    assert len(model["axes"]) == 6
    assert len(model["rules"]) >= 5
    assert all(rule["statement"] for rule in model["rules"])
    assert {axis["key"] for axis in model["axes"]} == {"gravity", "medium", "energy", "terrain", "resources", "society"}


def test_demo_world_has_scenes_and_is_clean():
    report = validate_world(DEMO)
    assert report.ok and report.warnings == []
    assert "scenes" in report.loaded_modules


def test_overlapping_bounds_rejected(tmp_path):
    world = _copy_demo(tmp_path)
    _edit_scenes(world, lambda data: data["scenes"][1].update(bounds=data["scenes"][0]["bounds"]))
    report = validate_world(world)
    assert any(issue.code == "SCENE_BOUNDS_OVERLAP" for issue in report.errors)


def test_bounds_outside_world_rejected(tmp_path):
    world = _copy_demo(tmp_path)
    _edit_scenes(world, lambda data: data["scenes"][0]["bounds"].update(max_chunk=[600, 31, 63]))
    report = validate_world(world)
    assert any(issue.code == "SCENE_BOUNDS_OUTSIDE" for issue in report.errors)


def test_unknown_biome_rejected(tmp_path):
    world = _copy_demo(tmp_path)
    _edit_scenes(world, lambda data: data["scenes"][0].update(biome_ref="void_biome"))
    report = validate_world(world)
    assert any(issue.code == "SCENE_UNKNOWN_BIOME" for issue in report.errors)


def test_unknown_scent_rejected(tmp_path):
    world = _copy_demo(tmp_path)
    _edit_scenes(world, lambda data: data["scenes"][3].update(sensory={"scent_refs": ["scent_void"]}))
    report = validate_world(world)
    assert any(issue.code == "SCENE_UNKNOWN_SCENT" for issue in report.errors)


def test_water_scene_requires_drowning_hazard(tmp_path):
    world = _copy_demo(tmp_path)

    def mutate(data):
        entry = scene_entry("lake", [0, 64, 0], [63, 95, 63])
        entry["hazards"] = []
        data["scenes"].append(entry)

    _edit_scenes(world, mutate)
    report = validate_world(world)
    assert any(issue.code == "SCENE_HAZARD_MISSING" for issue in report.warnings)


def test_templates_include_scene_layout(tmp_path):
    result = create_world(tmp_path / "earth", "earth_tpl", template="earth")
    assert result["ok"], result["errors"]
    data = json.loads((tmp_path / "earth" / "config" / "scenes.json").read_text(encoding="utf-8"))
    assert len(data["scenes"]) == 5
    assert {scene["id"] for scene in data["scenes"]} >= {"city_core", "farmland", "coast"}


def test_cli_scenes_list_and_model(capsys):
    assert main(["scenes", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == len(SCENES)

    assert main(["scenes", "model", "--json"]) == 0
    model = json.loads(capsys.readouterr().out)
    assert model["name"] == "统一环境场 UEF"


def test_cli_scenes_show(capsys):
    assert main(["scenes", "show", "city_core"]) == 0
    output = capsys.readouterr().out
    assert "城市中心" in output
    assert "轴6 社会" in output
    assert "→ 物理推导" in output


def test_session_query_scenes_converts_chunks_to_meters():
    session = WorldSession(DEMO)
    scenes = session.query_scenes()
    assert len(scenes) == 5

    city = next(item for item in scenes if item["id"] == "city_core")
    assert city["bounds"] == {"min_chunk": [0, 0, 0], "max_chunk": [63, 31, 63]}
    assert city["world_bounds_m"] == {"min": [0.0, 0.0, 0.0], "max": [204.8, 102.4, 204.8]}
    assert city["volume_chunks"] == 64 * 32 * 64
    assert city["medium"] == "air"
    assert city["biome_ref"] == "grassland"


def test_session_locate_scene_by_position():
    session = WorldSession(DEMO)
    hit = session.locate_scene([10.0, 5.0, 10.0])
    assert hit["chunk"] == [3, 1, 3]
    assert hit["inside_world"] is True
    assert hit["scene"]["id"] == "city_core"
    assert hit["scene"]["uef"]["medium"]["kind"] == "air"

    miss = session.locate_scene([0.0, 5000.0, 0.0])
    assert miss["scene"] is None
    assert miss["inside_world"] is False

    with pytest.raises(WorldError):
        session.locate_scene([1.0, 2.0])


def test_cli_query_scenes_and_scene_at(capsys):
    assert main(["query", str(DEMO), "scenes", "--json"]) == 0
    scenes = json.loads(capsys.readouterr().out)
    assert {item["id"] for item in scenes} >= {"city_core", "coast"}

    assert main(["query", str(DEMO), "scenes"]) == 0
    assert "city_core" in capsys.readouterr().out

    assert main(["query", str(DEMO), "scene-at", "--position", "10", "5", "10", "--json"]) == 0
    located = json.loads(capsys.readouterr().out)
    assert located["scene"]["id"] == "city_core"

    assert main(["query", str(DEMO), "scene-at"]) == 2
    capsys.readouterr()


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "dreamweave 0.1.0" in capsys.readouterr().out


def test_unknown_npc_scene_rejected(tmp_path):
    world = _copy_demo(tmp_path)
    path = world / "config" / "npc_agent.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["npc_roster"][0]["scene_id"] = "void_scene"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = validate_world(world)
    assert any(issue.code == "NPC_UNKNOWN_SCENE" for issue in report.errors)


def test_npc_context_includes_scene():
    payload = build_npc_context(WorldSession(DEMO), "guard_lin")
    scene = payload["context"]["environment"]["scene"]
    assert scene["id"] == "city_core"
    assert scene["uef"]["medium"]["kind"] == "air"
    assert "city_core" in payload["prompt"]


def test_query_npc_list_filters_by_scene():
    session = WorldSession(DEMO)
    assert {npc["id"] for npc in session.query_npc_list(scene_id="city_core")} == {
        "guard_lin",
        "merchant_zhao",
    }
    assert [npc["id"] for npc in session.query_npc_list(scene_id="farmland")] == ["elder_qiu"]
