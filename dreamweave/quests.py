"""M004-06 任务与操作剧情：任务完成 → 编年史节拍 → 矛盾推进。

剧情不再只由环境冲击驱动：玩家在哪个场景、做了什么任务、花了多久，
都会成为编年史里的一个节拍，并推动任务关联的结构矛盾。
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from .narrative import escalate_stage
from .world import WorldSession

WALK_SPEED_MPS = 1.4
MINUTES_PER_DAY = 1440

ACTION_LABELS = {
    "work": "劳作",
    "patrol": "巡逻",
    "socialize": "交涉",
    "worship": "祭拜",
    "eat": "用餐",
    "sleep": "休整",
    "shop": "采买",
}
WEATHER_LABELS = {
    "clear": "天晴",
    "rain": "降雨",
    "snow": "降雪",
    "storm": "风暴",
    "fog": "浓雾",
    "heatwave": "热浪",
    "coldwave": "寒潮",
    "sandstorm": "沙尘暴",
    "hurricane": "飓风",
    "aurora": "极光",
}


def _narrative(session: WorldSession) -> dict[str, Any]:
    return session.modules.get("narrative", {}) or {}


def _scene_index(session: WorldSession) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: item
        for item in session.modules.get("scenes", {}).get("scenes", [])
        if isinstance(item, dict) and item.get("id")
    }


def quest_list(session: WorldSession, completed: Iterable[str] = ()) -> list[dict[str, Any]]:
    """返回任务列表，附带场景名、动作中文名与完成状态。"""
    done = set(completed)
    scenes = _scene_index(session)
    result: list[dict[str, Any]] = []
    for quest in _narrative(session).get("quests", []):
        if not isinstance(quest, dict) or not quest.get("id"):
            continue
        scene = scenes.get(quest.get("scene_id"), {})
        result.append(
            {
                **quest,
                "scene_name": scene.get("name") or quest.get("scene_id"),
                "action_label": ACTION_LABELS.get(quest.get("action"), quest.get("action")),
                "completed": quest.get("id") in done,
            }
        )
    return result


def travel_minutes(
    origin: list[float] | tuple[float, ...] | None,
    target: list[float] | tuple[float, ...],
    *,
    speed_mps: float = WALK_SPEED_MPS,
) -> int:
    """按直线距离与步行速度估算旅途分钟数。"""
    if not origin:
        return 0
    distance = math.dist([float(value) for value in origin], [float(value) for value in target])
    if distance < 1.0:
        return 0
    return max(1, math.ceil(distance / (speed_mps * 60) - 1e-9))


def build_quest_beat(
    session: WorldSession,
    quest: dict[str, Any],
    *,
    minute: int,
    weather: dict[str, Any] | None = None,
    ambient_temperature_c: float = 15.0,
) -> dict[str, Any]:
    """把一个已完成任务写成编年史节拍。"""
    scene = _scene_index(session).get(quest.get("scene_id"), {})
    scene_name = scene.get("name") or quest.get("scene_id")
    action = ACTION_LABELS.get(quest.get("action"), quest.get("action"))
    kind = (weather or {}).get("kind")
    if kind:
        intensity = float((weather or {}).get("intensity", 0.0))
        weather_text = f"{WEATHER_LABELS.get(kind, kind)}（强度 {intensity:.2f}）"
    else:
        weather_text = "天气平稳"

    summary = (
        f"你完成了「{quest.get('title')}」：在{scene_name}{action} "
        f"{int(quest.get('duration_min', 0))} 分钟，{weather_text}，"
        f"环境 {float(ambient_temperature_c):.1f}℃。"
    )
    entry: dict[str, Any] = {
        "id": f"quest_{quest['id']}",
        "day": minute // MINUTES_PER_DAY + 1,
        "summary": summary[:512],
    }
    participants = [item for item in quest.get("participants") or [] if isinstance(item, str)]
    if participants:
        entry["participants"] = participants
    if quest.get("tension_id"):
        entry["tension_ref"] = quest["tension_id"]
    return entry


def quest_operations(
    session: WorldSession,
    quest: dict[str, Any],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """追加编年史，并让任务关联的矛盾上升一档。"""
    operations: list[dict[str, Any]] = [
        {"op": "append", "path": "narrative.chronicle", "value": entry}
    ]
    tension_id = quest.get("tension_id")
    if not tension_id:
        return operations

    for index, tension in enumerate(_narrative(session).get("tensions", [])):
        if not isinstance(tension, dict) or tension.get("id") != tension_id:
            continue
        current = float(tension.get("intensity", 0.0))
        target = min(1.0, round(current + 0.05, 2))
        operations.append(
            {"op": "set", "path": f"narrative.tensions.{index}.intensity", "value": target}
        )
        stage = (
            escalate_stage(tension.get("stage", "emerging"), target)
            if target > current
            else tension.get("stage")
        )
        if stage and stage != tension.get("stage"):
            operations.append(
                {"op": "set", "path": f"narrative.tensions.{index}.stage", "value": stage}
            )
        break
    return operations
