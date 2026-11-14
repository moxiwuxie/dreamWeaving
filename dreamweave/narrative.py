"""M007 剧情内核：现实结构压力计算与 AI 独有剧情生成契约。

剧情不是随机事件表，而是「结构矛盾 + 现实因果律」的推导结果：
- 宏观：国家/城邦之间的资源、制度、对外策略差异；
- 中观：同一国家内部阶层的收入、流动性、诉求差异；
- 微观：个体的欲望、阻碍、缺陷与秘密。
AI 只负责把压力演绎成独有剧情，不允许改动世界规则。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .schemas import SchemaSet
from .simulation import Simulation
from .world import WorldSession

STAGE_PRESSURE = {
    "dormant": 0.2,
    "emerging": 0.35,
    "escalating": 0.6,
    "open": 0.8,
    "crisis": 1.0,
    "resolution": 0.0,
}

STAGE_ORDER = ("dormant", "emerging", "escalating", "open", "crisis")

# 环境冲击通过「现实资源链」作用于结构矛盾：先缺什么，再吵什么。
WEATHER_STAKES: dict[str, tuple[str, ...]] = {
    "heatwave": ("water", "food"),
    "drought": ("water", "food"),
    "coldwave": ("food", "survival"),
    "snow": ("food", "survival"),
    "storm": ("trade", "survival", "land"),
    "hurricane": ("trade", "survival", "land"),
    "flood": ("trade", "survival", "land"),
    "sandstorm": ("trade", "land"),
    "fog": ("trade",),
    "rain": ("water", "food"),
    "aurora": (),
}

WEATHER_LABELS = {
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

# 生态衰退沿食物链传导：先冲击口粮，再冲击生存与贸易。
ECOLOGY_STAKES: dict[str, tuple[str, ...]] = {
    "declining": ("food", "survival"),
    "extinct": ("food", "survival", "trade"),
}

ECOLOGY_LABELS = {"declining": "进入衰退", "extinct": "彻底灭绝"}

ENV_INTENSITY_STEP = 0.06


def _narrative(session: WorldSession) -> dict[str, Any]:
    data = session.modules.get("narrative")
    if not isinstance(data, dict):
        raise ValueError("世界包未包含 narrative 模块")
    return data


def _index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item.get("id"): item for item in items if isinstance(item, dict) and item.get("id")}


def _scarcity(scene_ids: list[str], scenes: dict[str, dict[str, Any]]) -> tuple[float, str]:
    matched = [scenes[scene_id] for scene_id in scene_ids if scene_id in scenes]
    if not matched:
        return 0.5, "water"
    water = sum(item["uef"]["resources"]["water_availability"] for item in matched) / len(matched)
    biomass = sum(item["uef"]["resources"]["biomass"] for item in matched) / len(matched)
    water_gap, food_gap = 1.0 - water, 1.0 - biomass
    return (water_gap + food_gap) / 2, "water" if water_gap >= food_gap else "food"


def narrative_pressures(session: WorldSession, limit: int = 5) -> list[dict[str, Any]]:
    """按现实结构计算剧情压力，返回最值得被讲述的矛盾来源。"""
    narrative = _narrative(session)
    scenes = _index(session.modules.get("scenes", {}).get("scenes", []))
    nations = narrative.get("nations", [])
    classes = narrative.get("classes", [])
    tensions = narrative.get("tensions", [])
    nation_index = _index(nations)

    pressures: list[dict[str, Any]] = []
    for nation in nations:
        own_classes = [item for item in classes if item.get("nation_id") == nation.get("id")]
        scarcity, scarcity_stake = _scarcity(nation.get("territory_scene_refs", []), scenes)
        incomes = [float(item.get("income_index", 0)) for item in own_classes]
        inequality = (max(incomes) - min(incomes)) / 10 if len(incomes) > 1 else 0.0
        grievance = (
            sum(len(item.get("grievances", [])) for item in own_classes) / (len(own_classes) * 8)
            if own_classes
            else 0.0
        )
        instability = 1.0 - float(nation.get("stability", 1.0))
        score = 0.35 * scarcity + 0.25 * inequality + 0.2 * instability + 0.2 * grievance
        drivers = [
            {"key": "resource_scarcity", "value": round(scarcity, 3)},
            {"key": "income_inequality", "value": round(inequality, 3)},
            {"key": "instability", "value": round(instability, 3)},
            {"key": "grievance", "value": round(grievance, 3)},
        ]
        stake = scarcity_stake if scarcity >= 0.5 else ("power" if inequality >= 0.5 else "trade")
        pressures.append(
            {
                "id": nation.get("id"),
                "kind": "nation",
                "score": round(min(1.0, score), 3),
                "drivers": drivers,
                "suggested_stake": stake,
            }
        )

    for item in classes:
        nation = nation_index.get(item.get("nation_id"), {})
        siblings = [peer for peer in classes if peer.get("nation_id") == item.get("nation_id")]
        top_income = max((float(peer.get("income_index", 0)) for peer in siblings), default=0.0)
        disadvantage = max(0.0, top_income - float(item.get("income_index", 0))) / 10
        immobility = 1.0 - float(item.get("mobility", 0.0))
        grievance = len(item.get("grievances", [])) / 8
        instability = 1.0 - float(nation.get("stability", 1.0))
        score = 0.3 * disadvantage + 0.25 * immobility + 0.25 * grievance + 0.2 * instability
        pressures.append(
            {
                "id": item.get("id"),
                "kind": "class",
                "score": round(min(1.0, score), 3),
                "drivers": [
                    {"key": "income_disadvantage", "value": round(disadvantage, 3)},
                    {"key": "immobility", "value": round(immobility, 3)},
                    {"key": "grievance", "value": round(grievance, 3)},
                    {"key": "instability", "value": round(instability, 3)},
                ],
                "suggested_stake": "power" if disadvantage >= immobility else "food",
            }
        )

    for tension in tensions:
        score = float(tension.get("intensity", 0)) * STAGE_PRESSURE.get(tension.get("stage", "dormant"), 0.2)
        pressures.append(
            {
                "id": tension.get("id"),
                "kind": "tension",
                "score": round(min(1.0, score), 3),
                "drivers": [
                    {"key": "intensity", "value": round(float(tension.get("intensity", 0)), 3)},
                    {"key": "stage", "value": tension.get("stage")},
                ],
                "suggested_stake": tension.get("stake"),
            }
        )

    pressures.sort(key=lambda item: item["score"], reverse=True)
    return pressures[:limit]


def build_story_context(
    session: WorldSession,
    *,
    nation_id: str | None = None,
    class_id: str | None = None,
    npc_id: str | None = None,
    scene_id: str | None = None,
    day: int = 1,
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """生成一次剧情所需的上下文与提示词；story_key 保证同世界同日可去重。"""
    narrative = _narrative(session)
    nations = _index(narrative.get("nations", []))
    classes = _index(narrative.get("classes", []))
    npcs = _index(session.modules.get("npc_agent", {}).get("npc_roster", []))
    scenes = _index(session.modules.get("scenes", {}).get("scenes", []))

    for label, value, pool in (
        ("国家", nation_id, nations),
        ("阶层", class_id, classes),
        ("NPC", npc_id, npcs),
        ("场景", scene_id, scenes),
    ):
        if value is not None and value not in pool:
            raise ValueError(f"未知{label}：{value}")

    focus_ids = {item for item in (nation_id, class_id, npc_id) if item}
    tensions = [
        item
        for item in narrative.get("tensions", [])
        if not focus_ids or any(party.get("ref") in focus_ids for party in item.get("parties", []))
    ]
    arcs = [
        item
        for item in narrative.get("arcs", [])
        if npc_id is None or item.get("npc_ref") == npc_id
    ]
    chronicle = sorted(narrative.get("chronicle", []), key=lambda item: item.get("day", 0), reverse=True)
    window = int(narrative.get("generation", {}).get("novelty_window_days", 30))
    recent = [item for item in chronicle if day - int(item.get("day", 0)) <= window][:10]

    rules = sorted(narrative.get("causal_rules", []), key=lambda item: item.get("weight", 0), reverse=True)
    pressures = narrative_pressures(session, limit=6)
    generation = narrative.get("generation", {})
    must_respect = [
        rule for rule in rules if rule.get("id") in set(generation.get("must_respect", []))
    ] or rules[:1]

    story_key = hashlib.sha256(
        "|".join(
            [
                str(session.manifest.get("world_id", "")),
                str(day),
                nation_id or "-",
                class_id or "-",
                npc_id or "-",
                scene_id or "-",
                ",".join(item["id"] for item in pressures),
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]

    used_titles = [item.get("title") for item in arcs if item.get("title")]
    used_titles += [item.get("summary") for item in recent if item.get("summary")]

    context = {
        "world_id": session.manifest.get("world_id"),
        "day": day,
        "focus": {
            "nation": nations.get(nation_id) if nation_id else None,
            "class": classes.get(class_id) if class_id else None,
            "npc": npcs.get(npc_id) if npc_id else None,
            "scene": scenes.get(scene_id) if scene_id else None,
        },
        "pressures": pressures,
        "tensions": tensions[:5],
        "arcs": arcs[:5],
        "recent_chronicle": recent[:5],
        "causal_rules": must_respect,
        "generation": generation,
        "player_actions": list(actions or [])[-8:],
    }

    prompt = _render_prompt(context, story_key, used_titles)
    return {"story_key": story_key, "context": context, "prompt": prompt}


def _render_prompt(context: dict[str, Any], story_key: str, used_titles: list[str]) -> str:
    generation = context["generation"]
    pressure_lines = [
        f"  {index}. [{item['kind']}] {item['id']} 压力={item['score']} 建议矛盾={item['suggested_stake']}"
        for index, item in enumerate(context["pressures"], start=1)
    ]
    rule_lines = [
        f"  - {item['id']}：{item['principle']}（可见表现：{item['observable']}）"
        for item in context["causal_rules"]
    ]
    tension_lines = [
        f"  - {item['id']}（{item['stage']}，{item['intensity']}）"
        for item in context["tensions"]
    ]
    chronicle_lines = [f"  - 第 {item['day']} 天：{item['summary']}" for item in context["recent_chronicle"]]
    action_lines = [
        f"  - 第 {item.get('day')} 天 {item.get('time', '')}：{item.get('title')}"
        f" @{item.get('scene_id')}（{item.get('duration_min')} 分钟）"
        for item in context.get("player_actions", [])
    ]
    focus = context["focus"]
    focus_names = "、".join(
        f"{label}={value['id']}"
        for label, value in (
            ("国家", focus["nation"]),
            ("阶层", focus["class"]),
            ("个体", focus["npc"]),
            ("场景", focus["scene"]),
        )
        if value
    ) or "由结构压力自行选择"

    return "\n".join(
        [
            "你是《织梦》世界的剧情导演。请基于现实因果，生成一段独有的剧情大纲。",
            f"世界：{context['world_id']} · 第 {context['day']} 天 · 聚焦：{focus_names}",
            "",
            "【结构压力（从高到低，必须至少采纳其中 1 条）】",
            *(pressure_lines or ["  - 无"]),
            "",
            "【已有矛盾（可升级、可解决，不可无视）】",
            *(tension_lines or ["  - 无"]),
            "",
            "【近期编年史（禁止重复这些情节）】",
            *(chronicle_lines or ["  - 无"]),
            "",
            "【玩家近期操作（新剧情必须回应这些选择）】",
            *(action_lines or ["  - 无"]),
            "",
            "【必须遵守的现实因果律】",
            *(rule_lines or ["  - 无"]),
            "",
            f"【视角与文体】视角：{generation.get('pov')}；语言：{generation.get('language')}；语气：{generation.get('tone')}。",
            f"【禁止手法】{'；'.join(generation.get('forbidden', [])) or '无'}。",
            f"【唯一性要求】story_key 固定为 {story_key}；同一世界同一天的剧情不得重复；"
            f"新 id 必须全局唯一；不得复用以下已有标题/摘要：{'；'.join(used_titles[:8]) or '无'}。",
            "",
            "【输出】只输出一个 JSON 对象，不要解释，结构如下：",
            json.dumps(
                {
                    "story_key": story_key,
                    "title": "剧情标题",
                    "logline": "一句话梗概",
                    "tensions": [
                        {
                            "id": "new_tension_id",
                            "name": "矛盾名",
                            "parties": [{"kind": "nation|class|npc", "ref": "已有 id"}],
                            "stake": "water|food|power|trade|faith|honor|revenge|land|knowledge|survival",
                            "stage": "emerging|escalating|open|crisis|resolution|dormant",
                            "intensity": 0.6,
                            "triggers": ["触发条件"],
                        }
                    ],
                    "arcs": [
                        {
                            "id": "new_arc_id",
                            "title": "个体线标题",
                            "npc_ref": "已有 NPC id",
                            "desire": "欲望",
                            "obstacle": "阻碍",
                            "flaw": "缺陷",
                            "secret": "秘密",
                            "stakes": "代价",
                            "stage": "setup|rising|climax|resolution",
                            "tags": ["标签"],
                        }
                    ],
                    "chronicle": [
                        {
                            "id": "new_event_id",
                            "day": context["day"],
                            "summary": "发生了什么事",
                            "participants": ["已有 id"],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
        ]
    )


def outline_quality_errors(payload: Any, context: dict[str, Any]) -> list[dict[str, str]]:
    """提示词之外的内容兜底：必须采纳结构压力，且不得复用已有标题。"""
    if not isinstance(payload, dict):
        return []
    errors: list[dict[str, str]] = []

    suggested = {
        item.get("suggested_stake")
        for item in context.get("pressures", [])
        if item.get("suggested_stake")
    }
    stakes = {
        item.get("stake") for item in payload.get("tensions", []) if isinstance(item, dict)
    }
    if suggested and not (stakes & suggested):
        errors.append(
            {
                "code": "OUTLINE_PRESSURE",
                "path": "/tensions",
                "message": f"至少一条矛盾需采纳结构压力诉求：{'、'.join(sorted(suggested))}",
            }
        )

    used = {str(item.get("title", "")).strip() for item in context.get("arcs", [])}
    used |= {str(item.get("summary", "")).strip() for item in context.get("recent_chronicle", [])}
    title = str(payload.get("title", "")).strip()
    if title and title in used:
        errors.append(
            {"code": "OUTLINE_TITLE_REUSED", "path": "/title", "message": f"标题「{title}」已被使用"}
        )
    return errors


def handle_story_outline(
    session: WorldSession,
    payload: Any,
    dry_run: bool = False,
) -> dict[str, Any]:
    """校验 AI 剧情大纲，并只通过追加操作写入 narrative 模块。"""
    if not isinstance(payload, dict):
        return _rejected("大纲必须是 JSON 对象", [{"code": "OUTLINE_SCHEMA", "path": "/", "message": "根节点必须是对象"}])

    schema_errors = SchemaSet().iter_errors(payload, "narrative_outline.schema.json")
    if schema_errors:
        return _rejected(
            "大纲不符合 narrative_outline 契约",
            [{"code": "OUTLINE_SCHEMA", "path": path, "message": message} for path, message in schema_errors],
        )

    if "narrative" not in session.modules:
        return _rejected("世界包未包含 narrative 模块")

    operations: list[dict[str, Any]] = []
    for item in payload.get("tensions", []):
        operations.append({"op": "append", "path": "narrative.tensions", "value": item})
    for item in payload.get("arcs", []):
        operations.append({"op": "append", "path": "narrative.arcs", "value": item})
    for item in payload.get("chronicle", []):
        operations.append({"op": "append", "path": "narrative.chronicle", "value": item})

    result = session.apply_operations(operations, dry_run=dry_run)
    return {
        "accepted": result.accepted,
        "message": result.message,
        "story_key": payload.get("story_key"),
        "title": payload.get("title"),
        "logline": payload.get("logline"),
        "operations": result.operations,
        "errors": result.errors,
        "warnings": result.warnings,
    }


def _rejected(message: str, errors: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "accepted": False,
        "message": message,
        "story_key": None,
        "title": None,
        "logline": None,
        "operations": [],
        "errors": errors or [],
        "warnings": [],
    }


def escalate_stage(stage: str, intensity: float) -> str:
    """按强度提升矛盾阶段；只升不降，resolution 视为终态。"""
    if stage == "resolution":
        return stage
    current = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else 0
    if intensity >= 0.85:
        target = "crisis"
    elif intensity >= 0.65:
        target = "open"
    elif intensity >= 0.45:
        target = "escalating"
    elif intensity >= 0.25:
        target = "emerging"
    else:
        target = "dormant"
    return target if STAGE_ORDER.index(target) > current else stage


def _stake_deltas(
    tensions: list[dict[str, Any]],
    counts: dict[str, int],
    *,
    step: float,
) -> list[dict[str, Any]]:
    """按利益诉求命中次数折算矛盾强度变化；只返回真正发生变化的条目。"""
    deltas: list[dict[str, Any]] = []
    for tension in tensions:
        hits = counts.get(tension.get("stake"), 0)
        if not hits:
            continue
        before_intensity = round(float(tension.get("intensity", 0.0)), 3)
        before_stage = str(tension.get("stage", "dormant"))
        weighted = round(step * hits, 3)
        after_intensity = round(min(1.0, before_intensity + weighted), 3)
        after_stage = escalate_stage(before_stage, after_intensity)
        if after_intensity == before_intensity and after_stage == before_stage:
            continue
        deltas.append(
            {
                "tension_id": tension.get("id"),
                "stake": tension.get("stake"),
                "hits": hits,
                "weighted": weighted,
                "from_intensity": before_intensity,
                "to_intensity": after_intensity,
                "from_stage": before_stage,
                "to_stage": after_stage,
            }
        )
    return deltas


def environment_deltas(
    tensions: list[dict[str, Any]],
    weather_kinds: list[str],
    *,
    step: float = ENV_INTENSITY_STEP,
) -> list[dict[str, Any]]:
    """把天气事件折算为矛盾强度变化；只返回真正发生变化的条目。"""
    counts: dict[str, int] = {}
    for kind in weather_kinds:
        for stake in WEATHER_STAKES.get(kind, ()):
            counts[stake] = counts.get(stake, 0) + 1

    return [
        {**delta, "weather_hits": delta["hits"]}
        for delta in _stake_deltas(tensions, counts, step=step)
    ]


def ecology_deltas(
    tensions: list[dict[str, Any]],
    statuses: list[str],
    *,
    step: float = ENV_INTENSITY_STEP,
) -> list[dict[str, Any]]:
    """把生态衰退折算为矛盾强度变化，沿食物链作用于「口粮/生存/贸易」。"""
    counts: dict[str, int] = {}
    for status in statuses:
        for stake in ECOLOGY_STAKES.get(status, ()):
            counts[stake] = counts.get(stake, 0) + 1

    return [
        {**delta, "ecology_hits": delta["hits"]}
        for delta in _stake_deltas(tensions, counts, step=step)
    ]


def _merge_deltas(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """同一矛盾可能同时被天气与生态命中，合并为一条最终变化。"""
    merged: dict[str, dict[str, Any]] = {}
    for delta in (item for group in groups for item in group):
        current = merged.get(delta["tension_id"])
        if current is None:
            merged[delta["tension_id"]] = {
                "tension_id": delta["tension_id"],
                "stake": delta["stake"],
                "hits": delta["hits"],
                "weighted": delta["weighted"],
                "from_intensity": delta["from_intensity"],
                "to_intensity": delta["to_intensity"],
                "from_stage": delta["from_stage"],
                "to_stage": delta["to_stage"],
            }
            continue
        current["hits"] += delta["hits"]
        current["weighted"] = round(current["weighted"] + delta["weighted"], 3)
        current["to_intensity"] = round(min(1.0, current["from_intensity"] + current["weighted"]), 3)
        current["to_stage"] = escalate_stage(current["from_stage"], current["to_intensity"])
    return list(merged.values())


def _env_chronicle_id(kind: str, day: int) -> str:
    return f"env_{kind}_{day}"


def _event_day(event: dict[str, Any]) -> int:
    return event["minute"] // 1440 + 1


def _parse_ecology_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """解析形如 `grass stable→declining` 的生态事件，只关心衰退方向。"""
    species_id, _, transition = str(event.get("value", "")).partition(" ")
    if "→" not in transition:
        return None
    to_status = transition.rsplit("→", 1)[-1]
    if to_status not in ECOLOGY_STAKES:
        return None
    return {"species_id": species_id, "status": to_status, "day": _event_day(event)}


def advance_narrative(
    session: WorldSession,
    *,
    days: int = 1,
    day_start: int = 1,
    seed: int = 7,
    dry_run: bool = False,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """推进无头运行时，把环境与社会冲击写进结构矛盾与编年史。

    环境 -> 结构压力 -> 剧情 的接线点：天气通过资源链放大矛盾，
    生态衰退通过食物链传导，NPC 避险则作为社会扰动直接进入编年史。
    传入 state 时从存档点续跑，返回的 state 可继续链式推进。
    """
    if days <= 0:
        raise ValueError("days 必须为正整数")
    narrative = _narrative(session)
    tensions = narrative.get("tensions", [])

    if state is not None:
        simulation = Simulation.from_state(session, state, seed=seed)
    else:
        simulation = Simulation(session, day=day_start, seed=seed)
    window_start = simulation.absolute_minute
    simulation.tick(days * 1440, step=360)

    # tick 的终点落在下一天 0 点，窗口右开避免把下一天的事件算进本次推进。
    window_end = window_start + days * 1440
    events = [event for event in simulation.events if window_start <= event["minute"] < window_end]

    existing_ids = {
        item.get("id")
        for item in narrative.get("chronicle", [])
        if isinstance(item, dict) and item.get("id")
    }

    raw_weather = [
        {"kind": event["value"], "day": _event_day(event)}
        for event in events
        if event.get("type") == "weather" and event.get("value") != "clear"
    ]
    weather_observed = [
        item
        for item in raw_weather
        if _env_chronicle_id(item["kind"], item["day"]) not in existing_ids
    ]

    raw_ecology: list[dict[str, Any]] = []
    ecology_observed: list[dict[str, Any]] = []
    seen_ecology: set[tuple[str, int]] = set()
    shelter_by_day: dict[int, set[str]] = {}
    for event in events:
        if event.get("type") == "ecology":
            parsed = _parse_ecology_event(event)
            if parsed is None:
                continue
            raw_ecology.append(parsed)
            key = (parsed["species_id"], parsed["day"])
            if key in seen_ecology:
                continue
            seen_ecology.add(key)
            if _env_chronicle_id(f"ecology_{parsed['species_id']}", parsed["day"]) not in existing_ids:
                ecology_observed.append(parsed)
        elif event.get("type") == "npc_activity":
            npc_id, _, activity = str(event.get("value", "")).partition(" ")
            if activity == "shelter":
                shelter_by_day.setdefault(_event_day(event), set()).add(npc_id)

    shelter_observed = [
        {"day": day, "npcs": sorted(npcs)}
        for day, npcs in sorted(shelter_by_day.items())
        if npcs and _env_chronicle_id("shelter", day) not in existing_ids
    ]

    deltas = _merge_deltas(
        [
            environment_deltas(tensions, [item["kind"] for item in weather_observed]),
            ecology_deltas(tensions, [item["status"] for item in ecology_observed]),
        ]
    )
    if not deltas and not shelter_observed:
        if not raw_weather and not raw_ecology and not any(shelter_by_day.values()):
            message = "推进期间没有环境或社会冲击"
        elif not weather_observed and not ecology_observed and not shelter_observed:
            message = "当日冲击已写入编年史，无需重复推进"
        else:
            message = "冲击未命中任何矛盾"
        return {
            "accepted": True,
            "message": message,
            "weather": weather_observed,
            "ecology": ecology_observed,
            "shelter": shelter_observed,
            "changes": [],
            "chronicle": [],
            "operations": [],
            "state": simulation.to_state(),
            "errors": [],
            "warnings": [],
        }

    index_by_id = {item.get("id"): index for index, item in enumerate(tensions)}
    changed_ids = {item["tension_id"] for item in deltas}
    affected_by_weather = {
        kind: [
            item.get("id")
            for item in tensions
            if item.get("id") in changed_ids and item.get("stake") in WEATHER_STAKES.get(kind, ())
        ]
        for kind in {item["kind"] for item in weather_observed}
    }
    affected_by_ecology = {
        status: [
            item.get("id")
            for item in tensions
            if item.get("id") in changed_ids and item.get("stake") in ECOLOGY_STAKES.get(status, ())
        ]
        for status in {item["status"] for item in ecology_observed}
    }

    operations: list[dict[str, Any]] = []
    for delta in deltas:
        index = index_by_id[delta["tension_id"]]
        operations.append(
            {"op": "set", "path": f"narrative.tensions.{index}.intensity", "value": delta["to_intensity"]}
        )
        operations.append(
            {"op": "set", "path": f"narrative.tensions.{index}.stage", "value": delta["to_stage"]}
        )

    participants = [item.get("id") for item in narrative.get("nations", []) if item.get("id")]
    species_names = {
        item.get("id"): item.get("name", item.get("id"))
        for item in session.modules.get("ecology", {}).get("species", [])
        if isinstance(item, dict) and item.get("id")
    }
    chronicle: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in weather_observed:
        key = (item["kind"], item["day"])
        affected = affected_by_weather.get(item["kind"]) or []
        if key in seen or not affected:
            continue
        seen.add(key)
        label = WEATHER_LABELS.get(item["kind"], item["kind"])
        entry = {
            "id": _env_chronicle_id(item["kind"], item["day"]),
            "day": item["day"],
            "summary": f"{label}袭击世界，{'、'.join(affected)} 的紧张程度随之上升。",
            "participants": participants,
        }
        chronicle.append(entry)
        operations.append({"op": "append", "path": "narrative.chronicle", "value": entry})

    for item in ecology_observed:
        affected = affected_by_ecology.get(item["status"]) or []
        if not affected:
            continue
        name = species_names.get(item["species_id"], item["species_id"])
        label = ECOLOGY_LABELS.get(item["status"], item["status"])
        entry = {
            "id": _env_chronicle_id(f"ecology_{item['species_id']}", item["day"]),
            "day": item["day"],
            "summary": f"{name}种群{label}，{'、'.join(affected)} 的紧张程度随之上升。",
            "participants": participants,
        }
        chronicle.append(entry)
        operations.append({"op": "append", "path": "narrative.chronicle", "value": entry})

    weather_by_day = {item["day"]: item["kind"] for item in weather_observed}
    for item in shelter_observed:
        kind = weather_by_day.get(item["day"])
        label = WEATHER_LABELS.get(kind, "恶劣天气") if kind else "恶劣天气"
        entry = {
            "id": _env_chronicle_id("shelter", item["day"]),
            "day": item["day"],
            "summary": f"{label}迫使 {len(item['npcs'])} 名居民中断日程，返回常驻场景避险。",
            "participants": item["npcs"],
        }
        chronicle.append(entry)
        operations.append({"op": "append", "path": "narrative.chronicle", "value": entry})

    result = session.apply_operations(operations, dry_run=dry_run)
    return {
        "accepted": result.accepted,
        "message": result.message,
        "weather": weather_observed,
        "ecology": ecology_observed,
        "shelter": shelter_observed,
        "changes": deltas,
        "chronicle": chronicle,
        "operations": result.operations,
        "state": simulation.to_state(),
        "errors": result.errors,
        "warnings": result.warnings,
    }
