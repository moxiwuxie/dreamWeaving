"""感知时间层：事件采样压缩、主观时长估算与记忆回放。

对应「时间流速」专项里可落地、非侵入的部分：

- 不加速播放一切，只采样「可感知事件」，空白时间直接跳过；
- 用事件密度估算主观时长倍率（心理物理校准的占位实现，后续可换成问卷标定）；
- 把压缩区间整理成记忆快照，利用回顾式时间膨胀让玩家事后感觉经历了更长时间。

眼动注视分时流速、心率/皮电调节只保留扩展位：硬件接入前不参与计算。
"""

from __future__ import annotations

from typing import Any, Iterable

MINUTES_PER_DAY = 1440

# 事件显著度：越高越容易被写进记忆回放。
SALIENCE_BY_TYPE = {
    "weather": 3,
    "ecology": 3,
    "effect_on": 2,
    "effect_off": 2,
    "npc_move": 1,
    "npc_activity": 1,
    "survival": 1,
}
DEFAULT_SALIENCE = 1
KIND_LABELS = {
    "weather": "天候",
    "ecology": "生态",
    "npc_move": "迁徙",
    "npc_activity": "日程",
    "effect_on": "状态",
    "effect_off": "状态解除",
    "survival": "生存",
}
MAX_BEATS = 24

# 主观时长倍率：事件越密，主观上时间被拉得越长。
SUBJECTIVE_BASE = 0.7
SUBJECTIVE_PER_BEAT = 0.12
SUBJECTIVE_MIN = 0.5
SUBJECTIVE_MAX = 3.0


def _beat(event: dict[str, Any]) -> dict[str, Any]:
    minute = int(event.get("minute", 0) or 0)
    kind = str(event.get("type") or "event")
    return {
        "minute": minute,
        "day": minute // MINUTES_PER_DAY + 1,
        "time": f"{(minute % MINUTES_PER_DAY) // 60:02d}:{minute % 60:02d}",
        "kind": kind,
        "label": KIND_LABELS.get(kind, kind),
        "value": event.get("value"),
        "salience": SALIENCE_BY_TYPE.get(kind, DEFAULT_SALIENCE),
    }


def sample_beats(events: Iterable[dict[str, Any]], *, limit: int = MAX_BEATS) -> list[dict[str, Any]]:
    """按显著度采样可感知节拍：同一对象同类事件只保留首次，结果保持时间顺序。"""
    picked: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        beat = _beat(event)
        subject = str(beat["value"] or "").split(" ")[0]
        key = (beat["kind"], subject)
        if key in seen:
            continue
        seen.add(key)
        picked.append(beat)

    if limit > 0 and len(picked) > limit:
        ranked = sorted(picked, key=lambda item: (-item["salience"], item["minute"]))[:limit]
        picked = sorted(ranked, key=lambda item: item["minute"])
    return picked


def subjective_ratio(density: float) -> float:
    """按每天可感知节拍数估算主观时长倍率，并夹在稳定区间内。"""
    value = SUBJECTIVE_BASE + SUBJECTIVE_PER_BEAT * max(0.0, float(density))
    return round(min(SUBJECTIVE_MAX, max(SUBJECTIVE_MIN, value)), 2)


def build_recap(
    events: Iterable[dict[str, Any]],
    *,
    start_minute: int,
    end_minute: int,
    limit: int = MAX_BEATS,
) -> dict[str, Any]:
    """把一段压缩时间整理成记忆回放：关键节拍 + 主观时长估算。"""
    raw = [event for event in events if isinstance(event, dict)]
    beats = sample_beats(raw, limit=limit)
    minutes = max(0, int(end_minute) - int(start_minute))
    days = minutes / MINUTES_PER_DAY
    density = len(beats) / days if days > 0 else 0.0
    ratio = subjective_ratio(density)
    subjective_days = round(days * ratio, 2)

    counts: dict[str, int] = {}
    for beat in beats:
        counts[beat["label"]] = counts.get(beat["label"], 0) + 1
    detail = "、".join(f"{label} {count}" for label, count in counts.items()) or "无"
    summary = (
        f"压缩 {days:.2f} 天：{len(raw)} 条事件，采样 {len(beats)} 个可感知节拍（{detail}）；"
        f"主观时长约 {subjective_days} 天（倍率 {ratio}）。"
    )

    return {
        "from_minute": int(start_minute),
        "to_minute": int(end_minute),
        "from_day": int(start_minute) // MINUTES_PER_DAY + 1,
        "to_day": int(end_minute) // MINUTES_PER_DAY + 1,
        "days": round(days, 2),
        "event_count": len(raw),
        "beats": beats,
        "density": round(density, 2),
        "subjective_ratio": ratio,
        "subjective_days": subjective_days,
        "summary": summary,
    }


def advance_perceptual(
    simulation: Any,
    minutes: int,
    *,
    step: int = 60,
    limit: int = MAX_BEATS,
) -> dict[str, Any]:
    """在无头运行时里推进一段虚拟时间，只回放可感知事件。"""
    if minutes <= 0:
        raise ValueError("minutes 必须为正整数")
    start = simulation.absolute_minute
    before = len(simulation.events)
    simulation.tick(minutes, step=step)
    return build_recap(
        simulation.events[before:],
        start_minute=start,
        end_minute=simulation.absolute_minute,
        limit=limit,
    )
