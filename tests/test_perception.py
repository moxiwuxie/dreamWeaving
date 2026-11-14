"""M009 感知层：第一/第三人称机架、视听硬渲染契约与其余感觉接口。"""

import json
import shutil
from pathlib import Path

import pytest

from dreamweave import HARD_SENSES, INTERFACE_SENSES, build_perception, validate_world
from dreamweave.__main__ import main
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _copy_demo(tmp_path: Path) -> Path:
    target = tmp_path / "world"
    shutil.copytree(DEMO, target)
    return target


def test_first_person_camera_uses_eye_height():
    session = WorldSession(DEMO)

    frame = build_perception(session, perspective="first_person", position_m=[0, 0, 0])

    assert frame["perspective"] == "first_person"
    assert frame["transform"]["position_m"] == [0.1, 0.0, 1.65]
    assert frame["visual"]["camera"]["fov_deg"] == 90
    assert frame["visual"]["camera"]["head_bob"] is True
    assert frame["visual"]["renderer"]["backend"] == "ue5"
    assert frame["visual"]["renderer"]["global_illumination"] == "lumen"


def test_third_person_camera_uses_boom_and_shoulder():
    session = WorldSession(DEMO)

    frame = build_perception(session, perspective="third_person", position_m=[0, 0, 0])

    assert frame["transform"]["pivot_m"] == [0.0, 0.0, 1.7]
    assert frame["transform"]["position_m"] == [-3.2, 0.6, 1.7]
    assert frame["visual"]["camera"]["fov_deg"] == 75
    assert frame["visual"]["camera"]["boom_length_m"] == 3.2


def test_third_person_pitch_is_clamped():
    session = WorldSession(DEMO)

    frame = build_perception(
        session, perspective="third_person", position_m=[0, 0, 0], pitch_deg=90
    )

    assert frame["transform"]["rotation_deg"]["pitch"] == 45
    assert frame["transform"]["position_m"] == [-2.263, 0.6, -0.563]


def test_audio_listener_follows_camera_and_uses_scene_reverb():
    session = WorldSession(DEMO)

    frame = build_perception(session, position_m=[0, 0, 0], scene_id="forest")

    audio = frame["audio"]
    assert audio["backend"]["spatializer"] == "steam_audio"
    assert audio["hrtf"] == {"enabled": True, "profile": "steam_audio_default"}
    assert audio["listener"]["follow"] == "camera"
    assert audio["listener"]["position_m"] == frame["transform"]["position_m"]
    assert audio["reverb"] == {"preset": "forest", "source": "scene"}
    assert [item["id"] for item in audio["layers"]] == ["far", "mid", "near"]


def test_other_senses_are_interface_only():
    session = WorldSession(DEMO)

    frame = build_perception(session, position_m=[0, 0, 0])

    assert frame["hard_senses"] == list(HARD_SENSES)
    assert set(frame["interfaces"]) == set(INTERFACE_SENSES)
    for sense, contract in frame["interfaces"].items():
        assert contract["status"] == "interface_only"
        assert contract["renderable"] is False
        assert contract["entrypoint"]


def test_visual_status_mapping_and_scene_light():
    session = WorldSession(DEMO)

    frame = build_perception(
        session,
        position_m=[0, 0, 0],
        scene_id="forest",
        status_ids=["hypothermia", "unknown_status"],
    )

    assert frame["visual"]["status_vfx"] == [{"status": "hypothermia", "vfx": "vfx_frost_edges"}]
    assert frame["visual"]["unmapped_status"] == ["unknown_status"]
    assert frame["visual"]["scene"]["ambient_light_ev"] == 2


def test_unknown_perspective_and_scene_are_rejected():
    session = WorldSession(DEMO)
    with pytest.raises(ValueError):
        build_perception(session, perspective="isometric")
    with pytest.raises(ValueError):
        build_perception(session, scene_id="nowhere")


def test_reverb_map_must_reference_existing_scene(tmp_path):
    world = _copy_demo(tmp_path)
    audio_path = world / "config" / "audio.json"
    audio = json.loads(audio_path.read_text(encoding="utf-8"))
    audio["scene_reverb_map"]["nowhere"] = "cave"
    audio_path.write_text(json.dumps(audio, ensure_ascii=False, indent=2), encoding="utf-8")

    report = validate_world(world)

    assert not report.ok
    assert any(issue.code == "AUDIO_UNKNOWN_SCENE" for issue in report.errors)


def test_cli_perception_json(capsys):
    assert main(["perception", str(DEMO), "--json", "--position", "0", "0", "0", "--scene", "forest"]) == 0
    frame = json.loads(capsys.readouterr().out)
    assert frame["perspective"] == "first_person"
    assert frame["audio"]["reverb"]["preset"] == "forest"


def test_cli_perception_third_person_text(capsys):
    assert main(["perception", str(DEMO), "--perspective", "third_person", "--scene", "forest"]) == 0
    output = capsys.readouterr().out
    assert "third_person" in output
    assert "steam_audio" in output
    assert "haptic" in output
