"""网页 3D 界面视图模型：把世界包与无头运行时压成一份纯 JSON 快照。

这里只做读取与换算，不修改会话、模拟器或磁盘；同一输入在任意进程、
任意时刻都应得到完全一致的结果（所有"随机"值都来自 hashlib 摘要）。
"""

from __future__ import annotations

import colorsys
import hashlib
import math
from typing import Any

from .quests import quest_list
from .simulation import Simulation
from .world import WorldError, WorldSession

WEATHER_LABELS = {
    "clear": "晴",
    "rain": "雨",
    "snow": "雪",
    "storm": "风暴",
    "fog": "雾",
    "heatwave": "热浪",
    "coldwave": "寒潮",
    "sandstorm": "沙尘暴",
    "hurricane": "飓风",
    "aurora": "极光",
}

# 地球色系：地面主色 + 装饰强调色，保持克制、低饱和。
PALETTES = {
    "built": ("#8a8578", "#b6ad9c"),
    "forest": ("#2f4a35", "#5f7d54"),
    "grassland": ("#6f8f4e", "#9cb56b"),
    "coast": ("#d8c7a1", "#7fa7b5"),
    "mountain": ("#77736e", "#b0aaa2"),
}
DEFAULT_PALETTE = PALETTES["grassland"]

LAYER_COLORS = {
    "crowd": "#9a9a9a",
    "mass_life": "#5f8a6b",
    "llm_core": "#b48ead",
    "player": "#d8b45a",
}
DEFAULT_LAYER_COLOR = "#8c8c8c"

NPC_INSET = 0.12
RECENT_ACTIONS = 12
RECENT_CHRONICLE = 8
RECENT_EVENTS = 12
EYE_HEIGHT_M = 1.65
THIRD_PERSON_DISTANCE_M = 5.4


# ---------- 确定性哈希工具 ----------


def _digest(*parts: Any) -> bytes:
    """对参数做稳定编码后取 sha256，避免依赖进程内随机状态。"""
    text = ":".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).digest()


def _unit(*parts: Any) -> float:
    """把摘要前 8 字节换算成 [0, 1) 的确定性小数。"""
    return int.from_bytes(_digest(*parts)[:8], "big") / 2**64


def _seed_int(*parts: Any) -> int:
    """把摘要换算成非负整数，用于前端装饰物种子。"""
    return int.from_bytes(_digest(*parts)[:4], "big") % (2**31 - 1)


def _rgb_hex(red: float, green: float, blue: float) -> str:
    """把 0..1 的 RGB 分量格式化为 #rrggbb 小写十六进制。"""
    values = []
    for value in (red, green, blue):
        channel = int(round(max(0.0, min(1.0, value)) * 255))
        values.append(f"{channel:02x}")
    return "#" + "".join(values)


def _shift_hue(color: str, salt: str, amount: float = 0.02) -> str:
    """按哈希对色相做轻微偏移，使同类场景之间不显得重复。"""
    red = int(color[1:3], 16) / 255.0
    green = int(color[3:5], 16) / 255.0
    blue = int(color[5:7], 16) / 255.0
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    delta = (_digest(salt)[0] / 255.0 - 0.5) * 2.0 * amount
    hue = (hue + delta) % 1.0
    shifted = colorsys.hls_to_rgb(hue, lightness, saturation)
    return _rgb_hex(*shifted)


def _palette_key(scene: dict[str, Any]) -> str:
    """按类别、场景 id 与生物群系选出地球色系。"""
    scene_id = str(scene.get("id") or "")
    biome_ref = str(scene.get("biome_ref") or "")
    category = str(scene.get("category") or "")
    if category == "built":
        return "built"
    if "coast" in scene_id or "beach" in scene_id:
        return "coast"
    if "mountain" in scene_id or "mountain" in biome_ref:
        return "mountain"
    if "forest" in biome_ref or "forest" in scene_id:
        return "forest"
    return "grassland"


def _scene_colors(scene: dict[str, Any]) -> tuple[str, str]:
    ground, accent = PALETTES.get(_palette_key(scene), DEFAULT_PALETTE)
    scene_id = str(scene.get("id") or "")
    return (
        _shift_hue(ground, f"{scene_id}:ground"),
        _shift_hue(accent, f"{scene_id}:accent"),
    )


# ---------- 场景与玩家 ----------


def _scene_view(scene: dict[str, Any]) -> dict[str, Any]:
    """把 query_scenes 的条目换算为前端需要的米制包围盒与配色。"""
    bounds = scene.get("world_bounds_m") or {}
    low = [float(value) for value in (bounds.get("min") or [0.0, 0.0, 0.0])]
    high = [float(value) for value in (bounds.get("max") or [0.0, 0.0, 0.0])]
    size = [max(high[axis] - low[axis], 0.001) for axis in range(3)]
    center = [round((low[axis] + high[axis]) / 2.0, 3) for axis in range(3)]
    ground_color, accent_color = _scene_colors(scene)

    temperature = scene.get("ambient_temperature_c")
    return {
        "id": str(scene.get("id") or ""),
        "name": str(scene.get("name") or ""),
        "category": str(scene.get("category") or ""),
        "biome_ref": scene.get("biome_ref"),
        "center_m": center,
        "size_m": [round(value, 3) for value in size],
        "bounds_m": {"min": low, "max": high},
        "ground_color": ground_color,
        "accent_color": accent_color,
        "temperature_c": float(temperature) if isinstance(temperature, (int, float)) else None,
        "hazard_count": int(scene.get("hazard_count") or 0),
        "prop_seed": _seed_int("prop", scene.get("id") or ""),
    }


def _player_position(session: WorldSession, simulation: Simulation) -> list[float]:
    """优先使用运行时坐标；缺失时退回场景出生点，再退回原点。"""
    if simulation.position_m:
        return [float(value) for value in simulation.position_m]
    try:
        return [float(value) for value in session.scene_spawn()]
    except WorldError:
        return [0.0, 0.0, 0.0]


def _resolve_player_scene_id(
    session: WorldSession,
    simulation: Simulation,
    position: list[float],
    scenes: dict[str, dict[str, Any]],
) -> str | None:
    """按「运行时场景 -> 坐标命中 -> 第一个场景」的顺序解析玩家场景。"""
    if simulation.scene is not None:
        scene_id = simulation.scene.get("id")
        if scene_id in scenes:
            return scene_id
    if position:
        located = session.locate_scene(position).get("scene")
        if located is not None and located.get("id") in scenes:
            return located["id"]
    if scenes:
        return next(iter(scenes))
    return None


# ---------- NPC ----------


def _resolve_npc_scene_id(
    state: dict[str, Any],
    scenes: dict[str, dict[str, Any]],
    player_scene_id: str | None,
) -> str | None:
    """NPC 场景缺失或找不到时，依次退回玩家场景与第一个场景。"""
    scene_id = state.get("scene_id")
    if scene_id in scenes:
        return scene_id
    if player_scene_id in scenes:
        return player_scene_id
    if scenes:
        return next(iter(scenes))
    return None


def _npc_position(seed: int, npc_id: str, scene: dict[str, Any] | None) -> list[float]:
    """在场景包围盒内缩 12% 的区域里稳定落点，y 精确等于场景底面。"""
    if scene is None:
        return [0.0, 0.0, 0.0]
    low = scene["bounds_m"]["min"]
    high = scene["bounds_m"]["max"]
    width = high[0] - low[0]
    depth = high[2] - low[2]
    span = 1.0 - 2.0 * NPC_INSET
    x = low[0] + width * NPC_INSET + _unit(seed, npc_id, scene["id"], "x") * width * span
    z = low[2] + depth * NPC_INSET + _unit(seed, npc_id, scene["id"], "z") * depth * span
    return [round(x, 3), low[1], round(z, 3)]


def _npc_view(
    seed: int,
    state: dict[str, Any],
    scenes: dict[str, dict[str, Any]],
    player_scene_id: str | None,
) -> dict[str, Any]:
    npc_id = str(state.get("npc_id") or "")
    scene_id = _resolve_npc_scene_id(state, scenes, player_scene_id)
    scene = scenes.get(scene_id) if scene_id is not None else None
    layer = str(state.get("layer") or "")
    return {
        "id": npc_id,
        "name": str(state.get("name") or ""),
        "layer": layer,
        "scene_id": scene_id,
        "position_m": _npc_position(seed, npc_id, scene),
        "activity": str(state.get("activity") or "idle"),
        "status": str(state.get("status") or "idle"),
        "color": LAYER_COLORS.get(layer, DEFAULT_LAYER_COLOR),
    }


# ---------- 天气与时间 ----------


def _wind(session: WorldSession) -> list[float]:
    """读取盛行风分量；配置只有方向/风速时按 0 处理。"""
    climate = (session.modules.get("ecology") or {}).get("climate") or {}
    wind = climate.get("prevailing_wind") or {}
    if not isinstance(wind, dict):
        return [0.0, 0.0, 0.0]
    return [
        float(wind.get("x") or 0.0),
        float(wind.get("y") or 0.0),
        float(wind.get("z") or 0.0),
    ]


def _time_view(simulation: Simulation, snapshot: dict[str, Any]) -> dict[str, Any]:
    minute = int(snapshot["clock"]["minute"])
    daylight = round(max(0.0, math.sin(math.pi * (minute - 360) / 720.0)), 6)
    return {
        "day": int(snapshot["clock"]["day"]),
        "minute": minute,
        "clock": f"{minute // 60:02d}:{minute % 60:02d}",
        "daylight": daylight,
        "sun": {
            "azimuth_deg": round((minute / 1440.0) * 360.0, 3),
            "elevation_deg": round(90.0 * daylight - 8.0, 3),
        },
    }


def _weather_view(
    session: WorldSession,
    simulation: Simulation,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    weather = simulation.weather or {}
    kind = str(weather.get("kind") or "clear")
    intensity = float(weather.get("intensity") or 0.0)
    return {
        "kind": kind,
        "label": WEATHER_LABELS.get(kind, kind),
        "intensity": round(intensity, 3),
        "temperature_c": float(snapshot.get("ambient_temperature_c") or 0.0),
        "wind": _wind(session),
    }


# ---------- 对外入口 ----------


def build_view(
    session: WorldSession,
    simulation: Simulation,
    *,
    completed: set[str] | None = None,
    actions: list[dict] | None = None,
    recap: dict | None = None,
    seed: int = 7,
) -> dict[str, Any]:
    """构造网页 3D 界面所需的完整视图；返回值可直接 json.dumps。"""
    seed = int(seed)
    snapshot = simulation.snapshot()
    manifest = session.manifest or {}

    scenes = {
        item["id"]: item
        for item in (_scene_view(scene) for scene in session.query_scenes())
        if item["id"]
    }
    position = _player_position(session, simulation)
    player_scene_id = _resolve_player_scene_id(session, simulation, position, scenes)
    player_scene = scenes.get(player_scene_id) if player_scene_id is not None else None
    feet_y = player_scene["bounds_m"]["min"][1] if player_scene is not None else float(position[1])

    npcs = [
        _npc_view(seed, state, scenes, player_scene_id)
        for _, state in sorted(
            simulation.npcs.items(),
            key=lambda item: (str((item[1] or {}).get("layer") or ""), str(item[0])),
        )
    ]

    narrative = session.modules.get("narrative", {}) or {}
    events = list(simulation.events)
    return {
        "world": {
            "id": manifest.get("world_id"),
            "name": manifest.get("world_name"),
            "template": manifest.get("template"),
            "seed": seed,
        },
        "time": _time_view(simulation, snapshot),
        "weather": _weather_view(session, simulation, snapshot),
        "player": {
            "position_m": position,
            "feet_m": [float(position[0]), feet_y, float(position[2])],
            "scene_id": player_scene_id,
            "health": float(snapshot["player"]["health"]),
            "vitals": dict(snapshot["player"]),
            "survival": dict(snapshot["survival"]),
            "effects": [
                {"id": str(item.get("id") or ""), "severity": float(item.get("severity") or 0.0)}
                for item in snapshot.get("effects", [])
            ],
        },
        "camera": {
            "eye_height_m": EYE_HEIGHT_M,
            "third_person_distance_m": THIRD_PERSON_DISTANCE_M,
        },
        "scenes": list(scenes.values()),
        "npcs": npcs,
        "quests": quest_list(session, completed or ()),
        "actions": list(actions or [])[-RECENT_ACTIONS:],
        "tensions": list(narrative.get("tensions", [])),
        "chronicle": list(narrative.get("chronicle", []))[-RECENT_CHRONICLE:],
        "recap": recap,
        "events": events[-RECENT_EVENTS:],
    }
