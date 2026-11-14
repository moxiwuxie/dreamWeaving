"""M010 NPC 日程运行时：把调度模板解析为某一时刻的活动与所在地点。

地点先查 `location_scene_map`，未命中时回退到 NPC 的常驻场景。
只做确定性解析，不推进状态，便于无头运行时与引擎共用同一份排班数据。
"""

from __future__ import annotations

from typing import Any

from .world import WorldSession

MINUTES_PER_DAY = 1440
DEFAULT_ACTIVITY = "idle"


def _resolve_entry(entries: list[dict[str, Any]], minute: int) -> dict[str, Any] | None:
    for entry in entries:
        start = int(entry.get("start_minute", 0))
        end = int(entry.get("end_minute", 0))
        if start <= minute < end:
            return entry
    return None


def _time_label(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def schedule_at(
    session: WorldSession,
    *,
    day: int = 1,
    minute: int = 0,
    layer: str | None = None,
    scene_id: str | None = None,
    activity: str | None = None,
) -> list[dict[str, Any]]:
    """返回指定时刻的 NPC 日程切片，可按层级、场景和活动过滤。"""
    if day < 1:
        raise ValueError("day 必须为正整数")
    if not 0 <= minute < MINUTES_PER_DAY:
        raise ValueError(f"minute 必须在 0~{MINUTES_PER_DAY - 1} 之间")

    npc_data = session.modules.get("npc_agent")
    if npc_data is None:
        raise ValueError("世界包未包含 npc_agent 模块")

    templates = {
        item.get("id"): item
        for item in npc_data.get("schedule_templates", [])
        if isinstance(item, dict) and item.get("id")
    }
    location_map = npc_data.get("location_scene_map") or {}

    result: list[dict[str, Any]] = []
    for agent in npc_data.get("npc_roster", []):
        if not isinstance(agent, dict) or not agent.get("id"):
            continue
        if layer is not None and agent.get("layer") != layer:
            continue

        template = templates.get(agent.get("schedule_template"))
        entries = (template or {}).get("entries", [])
        entry = _resolve_entry(entries, minute)
        current_activity = entry.get("activity", DEFAULT_ACTIVITY) if entry else DEFAULT_ACTIVITY
        if activity is not None and current_activity != activity:
            continue

        location = entry.get("location") if entry else None
        home_scene = agent.get("scene_id")
        resolved_scene = location_map.get(location, home_scene) if location else home_scene
        if scene_id is not None and resolved_scene != scene_id:
            continue

        result.append(
            {
                "npc_id": agent.get("id"),
                "name": agent.get("name"),
                "layer": agent.get("layer"),
                "day": day,
                "minute": minute,
                "time": _time_label(minute),
                "activity": current_activity,
                "location": location,
                "scene_id": resolved_scene,
                "home_scene_id": home_scene,
                "schedule_template": agent.get("schedule_template"),
                "ends_in_min": int(entry["end_minute"]) - minute if entry else None,
            }
        )

    result.sort(key=lambda item: (str(item.get("layer")), str(item.get("npc_id"))))
    return result


def build_schedule(
    session: WorldSession,
    *,
    day: int = 1,
    minute: int = 0,
    layer: str | None = None,
    scene_id: str | None = None,
    activity: str | None = None,
) -> dict[str, Any]:
    """CLI/引擎友好的日程快照，包含筛选条件与总数。"""
    npcs = schedule_at(
        session,
        day=day,
        minute=minute,
        layer=layer,
        scene_id=scene_id,
        activity=activity,
    )
    return {
        "day": day,
        "minute": minute,
        "time": _time_label(minute),
        "filters": {"layer": layer, "scene_id": scene_id, "activity": activity},
        "count": len(npcs),
        "npcs": npcs,
    }
