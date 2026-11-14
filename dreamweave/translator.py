"""M006-03 AI 内容翻译器：自然语言 -> 强 Schema 配置补丁。

只做规则化解析，不调用模型：解析出的补丁必须经过全量校验，冲突即拒绝。
M006-04 的 LLM 只负责把 open 域需求改写为这些受控句式。
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .world import WorldSession

NUMBER = r"(-?\d+(?:\.\d+)?)"
PERCENT = r"(\d+(?:\.\d+)?)\s*%"
SET_VERBS = r"(?:改为|设为|调整为|调为|变成|修改为|为|到)"

LAYER_ALIASES = {
    "llm": "llm_core",
    "大模型": "llm_core",
    "人群": "crowd",
    "居民": "mass_life",
    "日常居民": "mass_life",
}

WEATHER_KINDS = {
    "暴雨": "storm",
    "雷暴": "storm",
    "降雨": "rain",
    "下雨": "rain",
    "下雪": "snow",
    "降雪": "snow",
    "雾": "fog",
}

CAPABILITIES = [
    "把重力改为 -3.7",
    "把空气密度设为 0.8",
    "把声速调为 280",
    "把环境温度改为 24",
    "把基础气温设为 18",
    "把摩擦系数改为 0.3",
    "把饥饿速度改为 0.03",
    "关闭 LLM 层",
    "把 LLM 并发上限改为 16",
    "把暴雨概率改为 20%",
    "把木材价格改为 8",
    "把混响改为洞穴",
    "把明暗适应改为 0.6",
    "开启 OVR 嗅觉硬件",
    "把感染概率改为 5%",
]


class UnsupportedPrompt(Exception):
    """当前规则集无法解析的指令。"""


def translate(session: WorldSession, text: str, dry_run: bool = True) -> dict[str, Any]:
    """解析并校验一条自然语言指令，返回可直接给编辑器的结果。"""
    try:
        operations = _parse(session, text)
    except UnsupportedPrompt as exc:
        return {
            "text": text,
            "accepted": False,
            "message": str(exc),
            "operations": [],
            "errors": [],
            "warnings": [],
        }

    payload = session.apply_operations(operations, dry_run=dry_run).to_dict()
    payload["text"] = text
    return payload


def _numeric_rule(
    pattern: str,
    path: str,
    reason: str,
    transform: Callable[[float], Any] | None = None,
    group: int = 1,
) -> tuple[re.Pattern[str], Callable[[re.Match[str]], list[dict[str, Any]]]]:
    compiled = re.compile(pattern)

    def build(match: re.Match[str]) -> list[dict[str, Any]]:
        raw = float(match.group(group))
        value = transform(raw) if transform else (int(raw) if raw.is_integer() else raw)
        return [{"op": "set", "path": path, "value": value, "reason": reason}]

    return compiled, build


def _layer_rule(session: WorldSession, text: str) -> list[dict[str, Any]] | None:
    match = re.search(r"(开启|关闭|启用|禁用)\s*(LLM|大模型|人群|居民|日常居民)(?:层|NPC)?", text, re.IGNORECASE)
    if not match:
        return None
    layer = LAYER_ALIASES.get(match.group(2).lower())
    if layer is None:
        return None
    enabled = match.group(1) in {"开启", "启用"}
    return [
        {
            "op": "set",
            "path": f"npc_agent.layers.{layer}.enabled",
            "value": enabled,
            "reason": f"{'开启' if enabled else '关闭'} NPC 层 {layer}",
        }
    ]


def _weather_rule(session: WorldSession, text: str) -> list[dict[str, Any]] | None:
    match = re.search(rf"(暴雨|雷暴|降雨|下雨|下雪|降雪|雾)[^，。]*?概率[^0-9]*{PERCENT}", text)
    if not match:
        return None
    kind = WEATHER_KINDS[match.group(1)]
    probability = round(float(match.group(2)) / 100, 4)
    events = session.modules.get("ecology", {}).get("climate", {}).get("weather_events", [])
    indexes = [index for index, event in enumerate(events) if event.get("kind") == kind]
    if not indexes:
        raise UnsupportedPrompt(f"世界中不存在 {kind} 类天气事件，请先新增该天气事件")
    return [
        {
            "op": "set",
            "path": f"ecology.climate.weather_events.{index}.probability_per_day",
            "value": probability,
            "reason": f"将 {kind} 每日概率设为 {match.group(2)}%",
        }
        for index in indexes
    ]


def _price_rule(session: WorldSession, text: str) -> list[dict[str, Any]] | None:
    match = re.search(rf"(?:把|将)?([\u4e00-\u9fa5]{{1,8}}?)[^，。]*?价格[^0-9]*{NUMBER}", text)
    if not match:
        return None
    keyword = match.group(1).strip()
    goods = session.modules.get("npc_agent", {}).get("economy", {}).get("goods", [])
    matches = [
        (index, good)
        for index, good in enumerate(goods)
        if keyword and (keyword in good.get("name", "") or good.get("name", "") in keyword)
    ]
    if not matches:
        raise UnsupportedPrompt(f"未找到商品：{keyword}")
    price = float(match.group(2))
    return [
        {
            "op": "set",
            "path": f"npc_agent.economy.goods.{index}.base_price",
            "value": int(price) if price.is_integer() else price,
            "reason": f"将 {good.get('name')} 基准价格设为 {match.group(2)}",
        }
        for index, good in matches
    ]


NUMERIC_RULES = [
    _numeric_rule(rf"重力[^，。]*?{SET_VERBS}\s*{NUMBER}", "physics.gravity", "修改重力"),
    _numeric_rule(rf"空气密度[^，。]*?{SET_VERBS}\s*{NUMBER}", "physics.air_density", "修改空气密度"),
    _numeric_rule(rf"声速[^，。]*?{SET_VERBS}\s*{NUMBER}", "physics.speed_of_sound", "修改声速"),
    _numeric_rule(rf"粘度[^，。]*?{SET_VERBS}\s*{NUMBER}", "physics.fluid.viscosity", "修改流体粘度"),
    _numeric_rule(
        rf"(?:环境温度|世界温度)[^，。]*?{SET_VERBS}\s*{NUMBER}",
        "physics.thermal.ambient_temperature_c",
        "修改环境温度",
    ),
    _numeric_rule(rf"基础气温[^，。]*?{SET_VERBS}\s*{NUMBER}", "ecology.climate.base_temperature_c", "修改气候基准温度"),
    _numeric_rule(rf"摩擦(?:系数)?[^，。]*?{SET_VERBS}\s*{NUMBER}", "physics.friction.dynamic", "修改动摩擦系数"),
    _numeric_rule(rf"饥饿(?:速度|速率)[^，。]*?{SET_VERBS}\s*{NUMBER}", "player_body.vitals.hunger.drain_per_hour", "修改饥饿速度"),
    _numeric_rule(rf"口渴(?:速度|速率)[^，。]*?{SET_VERBS}\s*{NUMBER}", "player_body.vitals.thirst.drain_per_hour", "修改口渴速度"),
    _numeric_rule(
        rf"(?:LLM|大模型)[^，。]*?(?:上限|并发|人数)[^0-9]*{NUMBER}",
        "npc_agent.layers.llm_core.max_active_agents",
        "修改 LLM NPC 并发上限",
        transform=lambda value: int(value),
    ),
    _numeric_rule(
        rf"人群上限[^，。]*?{SET_VERBS}\s*{NUMBER}",
        "npc_agent.layers.crowd.max_agents",
        "修改人群层上限",
        transform=lambda value: int(value),
    ),
    _numeric_rule(rf"明暗适应[^，。]*?{SET_VERBS}\s*{NUMBER}", "visual.brightness_adapt_speed", "修改瞳孔明暗适应速度"),
    _numeric_rule(rf"遮挡衰减[^，。]*?{SET_VERBS}\s*{NUMBER}", "audio.occlusion_attenuation", "修改声音遮挡衰减"),
    _numeric_rule(rf"风向影响[^，。]*?{SET_VERBS}\s*{NUMBER}", "scent.diffusion.wind_influence", "修改气味扩散的风向权重"),
]

PERCENT_RULES = [
    _numeric_rule(
        rf"感染概率[^，。]*?{SET_VERBS}\s*{PERCENT}",
        "player_body.immunity.infection_chance_per_contact",
        "修改感染概率",
        transform=lambda value: round(value / 100, 4),
    )
]

REVERB_ALIASES = {
    "室外": "outdoor_open",
    "森林": "forest",
    "洞穴": "cave",
    "小房间": "small_room",
    "教堂": "cathedral",
    "水下": "underwater",
}

HARDWARE_ALIASES = [
    (r"(?:嗅觉硬件|OVR|气味播放器)", "scent.hardware.ovr_omara.enabled", "嗅觉硬件"),
    (r"(?:触觉背心|bHaptics)", "haptic.devices.bhaptics.enabled", "bHaptics 触觉背心"),
    (r"(?:味觉硬件|Vocktail)", "taste.hardware.vocktail.enabled", "Vocktail 味觉硬件"),
]


def _reverb_rule(session: WorldSession, text: str) -> list[dict[str, Any]] | None:
    match = re.search(r"混响[^，。]*?(?:改为|设为|调为|切换到)\s*(室外|森林|洞穴|小房间|教堂|水下)", text)
    if not match:
        return None
    preset = REVERB_ALIASES[match.group(1)]
    return [{"op": "set", "path": "audio.reverb_preset", "value": preset, "reason": f"切换混响预设为 {match.group(1)}"}]


def _hardware_rule(session: WorldSession, text: str) -> list[dict[str, Any]] | None:
    for alias, path, label in HARDWARE_ALIASES:
        match = re.search(rf"(开启|关闭|启用|禁用)\s*{alias}", text, re.IGNORECASE)
        if match:
            enabled = match.group(1) in {"开启", "启用"}
            return [{"op": "set", "path": path, "value": enabled, "reason": f"{'启用' if enabled else '停用'}{label}"}]
    return None


def _parse(session: WorldSession, text: str) -> list[dict[str, Any]]:
    """按标点拆成多个意图，全部解析成功才返回，保证补丁原子性。"""
    clauses = [clause.strip() for clause in re.split(r"[，,；;]", text) if clause.strip()]
    if not clauses:
        raise UnsupportedPrompt("指令为空")
    operations: list[dict[str, Any]] = []
    for clause in clauses:
        operations.extend(_parse_clause(session, clause))
    return operations


def _parse_clause(session: WorldSession, text: str) -> list[dict[str, Any]]:
    for pattern, builder in (*NUMERIC_RULES, *PERCENT_RULES):
        match = pattern.search(text)
        if match:
            return builder(match)

    for rule in (_layer_rule, _reverb_rule, _hardware_rule, _weather_rule, _price_rule):
        operations = rule(session, text)
        if operations is not None:
            return operations

    raise UnsupportedPrompt(
        f"无法解析片段「{text}」。当前支持：" + "；".join(CAPABILITIES)
    )
