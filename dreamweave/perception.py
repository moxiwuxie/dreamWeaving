"""M009 感知层：第一/第三人称相机机架，硬渲染视觉与听觉，其余感觉只留接口。

视觉输出对接 UE5 Lumen+Nanite / Unity HDRP 等成熟管线，
听觉输出对接 Steam Audio / Wwise / MetaSounds 等成熟空间音频方案。
本模块只生成与引擎无关的渲染契约，不依赖任何渲染器。
"""

from __future__ import annotations

import math
from typing import Any

from .sensory_bus import bus_config, describe_bus
from .world import WorldSession

HARD_SENSES = ("visual", "audio")
INTERFACE_SENSES = ("haptic", "scent", "taste")
PERSPECTIVES = ("first_person", "third_person")

# 旧世界包缺少 perspective 时的兜底机架，保证感知层始终可用。
FALLBACK_PERSPECTIVE: dict[str, Any] = {
    "default": "first_person",
    "blend_seconds": 0.25,
    "first_person": {
        "eye_height_m": 1.65,
        "forward_offset_m": 0.1,
        "fov_deg": 90,
        "near_clip_m": 0.05,
        "far_clip_m": 20000,
        "pitch_min_deg": -85,
        "pitch_max_deg": 85,
        "head_bob": True,
    },
    "third_person": {
        "boom_length_m": 3.2,
        "shoulder_offset_m": 0.6,
        "height_offset_m": 1.7,
        "fov_deg": 75,
        "near_clip_m": 0.1,
        "far_clip_m": 20000,
        "collision_probe_radius_m": 0.2,
        "min_boom_m": 0.5,
        "pitch_min_deg": -70,
        "pitch_max_deg": 45,
    },
}

REVERB_BY_MEDIUM = {"water": "underwater", "seawater": "underwater"}

SENSE_INTERFACES = {
    "haptic": {"module": "haptic", "entrypoint": "haptic.event_wave_map"},
    "scent": {"module": "scent", "entrypoint": "scent.scent_lib"},
    "taste": {"module": "taste", "entrypoint": "taste.food_bindings"},
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round_vec(vector: list[float]) -> list[float]:
    return [round(value, 3) for value in vector]


def _rotation(yaw_deg: float, pitch_deg: float) -> tuple[list[float], list[float], list[float]]:
    """UE 风格坐标：X 前、Y 右、Z 上；yaw 绕 Z 轴，pitch 抬头为正。"""
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    forward = [
        math.cos(pitch) * math.cos(yaw),
        math.cos(pitch) * math.sin(yaw),
        math.sin(pitch),
    ]
    right = [-math.sin(yaw), math.cos(yaw), 0.0]
    up = [0.0, 0.0, 1.0]
    return forward, right, up


def _resolve_scene(
    session: WorldSession,
    position_m: list[float] | None,
    scene_id: str | None,
) -> dict[str, Any] | None:
    scenes = session.modules.get("scenes", {}).get("scenes", [])
    if scene_id is not None:
        for scene in scenes:
            if scene.get("id") == scene_id:
                return scene
        raise ValueError(f"未知场景：{scene_id}")
    if position_m is None:
        return None
    located = session.locate_scene(position_m)["scene"]
    if located is None:
        return None
    # locate_scene 返回派生的 UEF 视图，回查原始配置以保留 sensory 等渲染字段。
    for scene in scenes:
        if scene.get("id") == located.get("id"):
            return scene
    return located


def _camera_rig(
    perspective: str,
    config: dict[str, Any],
    base: list[float],
    yaw_deg: float,
    pitch_deg: float,
) -> tuple[list[float], list[float], float, list[float], list[float], list[float], dict[str, Any]]:
    rig = config[perspective]
    pitch = _clamp(pitch_deg, float(rig["pitch_min_deg"]), float(rig["pitch_max_deg"]))
    forward, right, up = _rotation(yaw_deg, pitch)

    if perspective == "first_person":
        pivot = [base[0], base[1], base[2] + float(rig["eye_height_m"])]
        camera = [
            pivot[index] + forward[index] * float(rig["forward_offset_m"])
            for index in range(3)
        ]
        meta = {
            "forward_offset_m": rig["forward_offset_m"],
            "head_bob": bool(rig.get("head_bob", False)),
        }
    else:
        pivot = [base[0], base[1], base[2] + float(rig["height_offset_m"])]
        boom = max(float(rig["min_boom_m"]), float(rig["boom_length_m"]))
        shoulder = float(rig["shoulder_offset_m"])
        camera = [
            pivot[index] - forward[index] * boom + right[index] * shoulder
            for index in range(3)
        ]
        meta = {
            "boom_length_m": boom,
            "min_boom_m": rig["min_boom_m"],
            "shoulder_offset_m": rig["shoulder_offset_m"],
            "collision_probe_radius_m": rig["collision_probe_radius_m"],
        }
    return camera, pivot, pitch, forward, right, up, meta


def _enabled_devices(module: dict[str, Any]) -> list[str]:
    devices: dict[str, Any] = module.get("devices") or module.get("hardware") or {}
    return [
        key
        for key, value in devices.items()
        if isinstance(value, dict) and value.get("enabled") is True
    ]


def _sense_interface(session: WorldSession, sense: str) -> dict[str, Any]:
    spec = SENSE_INTERFACES[sense]
    module = session.modules.get(spec["module"]) or {}
    return {
        "sense": sense,
        "status": "interface_only",
        "renderable": False,
        "module": spec["module"],
        "entrypoint": spec["entrypoint"],
        "devices": _enabled_devices(module),
    }


def _scene_payload(scene: dict[str, Any] | None) -> dict[str, Any] | None:
    if scene is None:
        return None
    uef = scene.get("uef") or {}
    sensory = scene.get("sensory") or {}
    return {
        "id": scene.get("id"),
        "name": scene.get("name"),
        "category": scene.get("category"),
        "biome_ref": scene.get("biome_ref"),
        "medium": (uef.get("medium") or {}).get("kind"),
        "ambient_temperature_c": (uef.get("energy") or {}).get("ambient_temperature_c"),
        "ambient_light_ev": sensory.get("ambient_light_ev"),
        "scent_refs": sensory.get("scent_refs", []),
    }


def build_perception(
    session: WorldSession,
    *,
    perspective: str | None = None,
    position_m: list[float] | None = None,
    scene_id: str | None = None,
    yaw_deg: float = 0.0,
    pitch_deg: float = 0.0,
    velocity_mps: list[float] | None = None,
    status_ids: list[str] | None = None,
) -> dict[str, Any]:
    """生成一帧感知契约：相机、视觉渲染参数、听者与听觉渲染参数。"""
    visual = session.modules.get("visual")
    audio = session.modules.get("audio")
    if visual is None:
        raise ValueError("世界包未包含 visual 模块")
    if audio is None:
        raise ValueError("世界包未包含 audio 模块")

    config = visual.get("perspective") or FALLBACK_PERSPECTIVE
    mode = perspective or config.get("default", "first_person")
    if mode not in PERSPECTIVES:
        raise ValueError(f"未知视角：{mode}")

    if position_m is None:
        base = [0.0, 0.0, 0.0]
    else:
        base = [float(value) for value in position_m]
        if len(base) != 3:
            raise ValueError("position_m 必须为 [x, y, z]")

    scene = _resolve_scene(session, position_m, scene_id)
    scene_info = _scene_payload(scene)
    camera, pivot, pitch, forward, right, up, rig_meta = _camera_rig(
        mode, config, base, yaw_deg, pitch_deg
    )
    rotation = {"yaw": round(yaw_deg % 360, 3), "pitch": round(pitch, 3), "roll": 0.0}
    transform = {
        "position_m": _round_vec(camera),
        "pivot_m": _round_vec(pivot),
        "rotation_deg": rotation,
        "forward": _round_vec(forward),
        "right": _round_vec(right),
        "up": up,
    }

    status_map = visual.get("status_vfx_map") or {}
    statuses = list(status_ids or [])
    visual_frame = {
        "renderer": visual.get("renderer", {}),
        "camera": {
            "mode": mode,
            "fov_deg": config[mode]["fov_deg"],
            "near_clip_m": config[mode]["near_clip_m"],
            "far_clip_m": config[mode]["far_clip_m"],
            "blend_seconds": config.get("blend_seconds", 0.0),
            "brightness_adapt_speed": visual.get("brightness_adapt_speed", 1.0),
            **rig_meta,
        },
        "atmosphere": visual.get("atmosphere", {}),
        "exposure": visual.get("exposure", {}),
        "depth_of_field": visual.get("depth_of_field", {}),
        "motion_blur": visual.get("motion_blur", {}),
        "scene": scene_info,
        "status_vfx": [
            {"status": status, "vfx": status_map[status]}
            for status in statuses
            if status in status_map
        ],
        "unmapped_status": [status for status in statuses if status not in status_map],
    }

    backend = audio.get("backend", {})
    listener_cfg = audio.get("listener", {})
    follow = listener_cfg.get("follow", "camera")
    listener_position = camera if follow == "camera" else pivot

    reverb_preset = audio.get("reverb_preset", "outdoor_open")
    reverb_source = "global"
    if scene_info is not None:
        medium = scene_info.get("medium")
        scene_reverb = (scene or {}).get("sensory", {}).get("reverb_preset")
        if medium in REVERB_BY_MEDIUM:
            reverb_preset = REVERB_BY_MEDIUM[medium]
            reverb_source = "medium"
        elif scene_reverb:
            reverb_preset = scene_reverb
            reverb_source = "scene"
        elif (audio.get("scene_reverb_map") or {}).get(scene_info["id"]):
            reverb_preset = audio["scene_reverb_map"][scene_info["id"]]
            reverb_source = "world_map"

    audio_frame = {
        "backend": backend,
        "listener": {
            "position_m": _round_vec(listener_position),
            "rotation_deg": rotation,
            "velocity_mps": _round_vec([float(value) for value in (velocity_mps or [0.0, 0.0, 0.0])]),
            "follow": follow,
            "update_rate_hz": listener_cfg.get("update_rate_hz", 60),
        },
        "hrtf": {
            "enabled": bool(audio.get("hrtf_enabled", False)),
            "profile": backend.get("hrtf_profile"),
        },
        "reverb": {"preset": reverb_preset, "source": reverb_source},
        "occlusion": {
            "attenuation": audio.get("occlusion_attenuation", 0.0),
            "spatializer": backend.get("spatializer", "native"),
        },
        "layers": [
            {"id": layer, "gain": gain}
            for layer, gain in (audio.get("layer_gain") or {}).items()
        ],
        "material_sound_map": audio.get("material_sound_map", {}),
    }

    return {
        "world_id": session.manifest.get("world_id"),
        "perspective": mode,
        "hard_senses": list(HARD_SENSES),
        "transform": transform,
        "visual": visual_frame,
        "audio": audio_frame,
        "interfaces": {sense: _sense_interface(session, sense) for sense in INTERFACE_SENSES},
        "sensory_bus": describe_bus(bus_config(session)),
    }
