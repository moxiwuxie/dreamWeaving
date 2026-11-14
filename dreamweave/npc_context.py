"""M006-04 LLM NPC 驱动接口：把记忆、人设、关系与环境拼成受控上下文。

只产出上下文与 Prompt，不直接调用模型；模型输出再经 M006-03 校验后写回世界。
"""

from __future__ import annotations

import json
from typing import Any

from .npc_schedule import schedule_at
from .world import WorldSession

TOP_MEMORIES = 5

PROMPT_TEMPLATE = """你是《{world_name}》中的居民 {name}（{layer}）。
人设：{persona}
当前目标：{goals}
说话风格：{speech_style}

【记忆摘要】
{memories}

【人际关系】
{relations}

【今日日程】
当前：{current}
{schedule}

【环境】
{environment}

行为约束：
1. 只输出 {name} 的第一人称回应，不描述其他角色的心理活动。
2. 不得违背世界规则：{laws}。
3. 若需要改变世界状态，只能提出受控指令，由系统校验后执行。
"""


def _persona_text(persona: dict[str, Any]) -> str:
    traits = persona.get("traits", {})
    if not traits:
        return "无明显人格倾向"
    labels = {
        "openness": "开放",
        "conscientiousness": "尽责",
        "extraversion": "外向",
        "agreeableness": "宜人",
        "neuroticism": "神经质",
    }
    return "，".join(f"{labels.get(key, key)} {value:.2f}" for key, value in traits.items())


def build_npc_context(
    session: WorldSession,
    npc_id: str,
    environment: dict[str, Any] | None = None,
    *,
    day: int | None = None,
    minute: int | None = None,
) -> dict[str, Any]:
    roster = session.modules.get("npc_agent", {}).get("npc_roster", [])
    npc = next((item for item in roster if item.get("id") == npc_id), None)
    if npc is None:
        raise KeyError(f"世界中不存在 NPC：{npc_id}")

    names = {item.get("id"): item.get("name") for item in roster}
    memories = sorted(
        npc.get("memory", {}).get("seed_events", []),
        key=lambda event: event.get("salience", 0),
        reverse=True,
    )[:TOP_MEMORIES]
    relations = [
        {
            "target": relation.get("target"),
            "name": names.get(relation.get("target"), relation.get("target")),
            "trust": relation.get("trust"),
            "familiarity": relation.get("familiarity"),
        }
        for relation in npc.get("relations", [])
    ]

    schedule_id = npc.get("schedule_template")
    templates = session.modules.get("npc_agent", {}).get("schedule_templates", [])
    template = next((item for item in templates if item.get("id") == schedule_id), None)
    schedule = template.get("entries", []) if template else []

    current = None
    if minute is not None and day is None:
        day = 1
    if day is not None and minute is not None:
        current = next(
            (item for item in schedule_at(session, day=day, minute=minute) if item["npc_id"] == npc_id),
            None,
        )

    if environment is None:
        environment = {
            "ambient_temperature_c": session.modules.get("physics", {}).get("thermal", {}).get("ambient_temperature_c"),
            "weather": session.get_weather(),
        }
    scene_id = (current or {}).get("scene_id") or npc.get("scene_id")
    scene = next(
        (
            item
            for item in session.modules.get("scenes", {}).get("scenes", [])
            if item.get("id") == scene_id
        ),
        None,
    )
    if scene is not None:
        environment["scene"] = {
            "id": scene.get("id"),
            "name": scene.get("name"),
            "category": scene.get("category"),
            "uef": scene.get("uef"),
        }
    else:
        environment["scene"] = None

    laws = [law.get("description") for law in session.modules.get("npc_agent", {}).get("laws", [])]

    context = {
        "npc": {
            "id": npc.get("id"),
            "name": npc.get("name"),
            "layer": npc.get("layer"),
            "persona": npc.get("persona"),
        },
        "memory": memories,
        "relations": relations,
        "schedule": schedule,
        "current": current,
        "environment": environment,
        "laws": laws,
    }

    prompt = PROMPT_TEMPLATE.format(
        world_name=session.manifest.get("world_name", "未知世界"),
        name=npc.get("name"),
        layer=npc.get("layer"),
        persona=_persona_text(npc.get("persona", {})),
        goals="；".join(npc.get("persona", {}).get("goals", [])) or "维持日常生活",
        speech_style=npc.get("persona", {}).get("speech_style", "自然口语"),
        memories="\n".join(
            f"- 第 {event.get('day')} 天：{event.get('summary')}（显著度 {event.get('salience')}）"
            for event in memories
        )
        or "- 暂无",
        relations="\n".join(
            f"- {relation['name']}：信任 {relation['trust']}，熟悉度 {relation['familiarity']}"
            for relation in relations
        )
        or "- 暂无",
        schedule="\n".join(
            f"- {entry.get('start_minute')}~{entry.get('end_minute')} 分：{entry.get('activity')} @ {entry.get('location')}"
            for entry in schedule
        )
        or "- 无固定日程",
        current=(
            f"第 {current['day']} 天 {current['time']}：{current['activity']} @ {current['location']}"
            f"（{current['scene_id'] or '不在场景'}，剩 {current['ends_in_min']} 分钟）"
            if current
            else "未指定当前时刻"
        ),
        environment=json.dumps(environment, ensure_ascii=False, indent=2),
        laws="；".join(laws) or "无成文法",
    )

    return {"context": context, "prompt": prompt}
