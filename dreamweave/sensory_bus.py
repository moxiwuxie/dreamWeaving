"""M011 统一感官事件总线。

玩法与模拟模块只发布一份「感知事件」，总线负责：
1. 通道路由：visual / audio / haptic / scent / taste；
2. 心理物理映射：物理强度按 Stevens / Fechner 曲线转成主观感受；
3. 跨模态同步：硬通道对齐到同一到达时刻，超过时间窗的慢设备裁剪；
4. 焦点降级：非焦点事件按注意力折扣，低于阈值直接忽略；
5. 算力预算：按帧事件数与通道事件率裁剪，优先保留高优先级事件；
6. 硬件降级：气味/味觉无外设时由 soft_hint 虚拟设备承接，供引擎渲染替代反馈。
"""

from __future__ import annotations

import copy
import math
from typing import Any, Iterable

from .schemas import SchemaSet

CHANNELS: tuple[str, ...] = ("visual", "audio", "haptic", "scent", "taste")
HARD_CHANNELS: tuple[str, ...] = ("visual", "audio", "haptic")
SOFT_CHANNELS: tuple[str, ...] = ("scent", "taste")

EVENT_SCHEMA = "sensory_event.schema.json"
BUS_SCHEMA = "sensory_bus.schema.json"

DEFAULT_ROUTING: dict[str, list[str]] = {
    "impact": ["visual", "audio", "haptic", "scent"],
    "step": ["audio", "haptic", "scent"],
    "voice": ["audio"],
    "ambient": ["visual", "audio", "scent"],
    "weather": ["visual", "audio", "haptic", "scent"],
    "status": ["visual", "audio", "haptic", "scent", "taste"],
    "action": ["visual", "audio", "haptic", "scent"],
    "scent_release": ["scent"],
    "taste": ["taste"],
    "ui": ["visual", "audio"],
    "custom": ["visual", "audio", "haptic", "scent", "taste"],
}


class SensoryBusError(ValueError):
    """总线配置非法，或事件计划无法生成。"""

    def __init__(self, message: str, errors: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def default_bus_config() -> dict[str, Any]:
    """无 sensory_bus 模块时的兜底配置：视听触硬同步，嗅味走软提示设备。"""
    return {
        "schema_version": "0.1.0",
        "sync_window_ms": 45,
        "soft_sync_window_ms": 400,
        "tick_hz": 120,
        "channels": {
            "visual": {
                "enabled": True,
                "sync_mode": "hard",
                "min_priority": 0.0,
                "devices": [
                    {
                        "id": "xr_headset",
                        "enabled": True,
                        "latency_ms": 10,
                        "capabilities": ["hdr", "eye_tracking"],
                    }
                ],
            },
            "audio": {
                "enabled": True,
                "sync_mode": "hard",
                "min_priority": 0.0,
                "devices": [
                    {
                        "id": "hrtf_headset",
                        "enabled": True,
                        "latency_ms": 18,
                        "capabilities": ["hrtf", "spatial"],
                    }
                ],
            },
            "haptic": {
                "enabled": True,
                "sync_mode": "hard",
                "min_priority": 0.0,
                "devices": [
                    {
                        "id": "dualsense",
                        "enabled": True,
                        "latency_ms": 28,
                        "capabilities": ["adaptive_trigger", "wideband"],
                    }
                ],
            },
            "scent": {
                "enabled": True,
                "sync_mode": "soft",
                "min_priority": 0.1,
                "devices": [
                    {"id": "ovr_omara", "enabled": False, "latency_ms": 180, "capabilities": ["cartridge"]},
                    {"id": "scent_soft_hint", "enabled": True, "latency_ms": 60, "capabilities": ["soft_hint"]},
                ],
            },
            "taste": {
                "enabled": True,
                "sync_mode": "soft",
                "min_priority": 0.1,
                "devices": [
                    {"id": "vocktail", "enabled": False, "latency_ms": 150, "capabilities": ["microcurrent"]},
                    {"id": "taste_soft_hint", "enabled": True, "latency_ms": 60, "capabilities": ["soft_hint"]},
                ],
            },
        },
        "psychophysics": {
            "visual": {"law": "stevens", "exponent": 0.45},
            "audio": {"law": "stevens", "exponent": 0.6},
            "haptic": {"law": "stevens", "exponent": 0.75},
            "scent": {"law": "stevens", "exponent": 0.4},
            "taste": {"law": "stevens", "exponent": 0.5},
        },
        "routing": copy.deepcopy(DEFAULT_ROUTING),
        "focus_policy": {
            "enabled": True,
            "off_focus_scale": 0.35,
            "attenuated_channels": ["haptic", "scent", "taste"],
            "drop_below": 0.05,
        },
        "budget": {
            "max_events_per_tick": 32,
            "per_channel_events_per_sec": {
                "visual": 90,
                "audio": 60,
                "haptic": 40,
                "scent": 8,
                "taste": 4,
            },
        },
    }


def bus_config(session: Any) -> dict[str, Any]:
    """读取世界包中的总线配置；缺失时使用兜底配置。"""
    stored = session.modules.get("sensory_bus") if hasattr(session, "modules") else None
    config = copy.deepcopy(stored) if isinstance(stored, dict) else default_bus_config()
    config.setdefault("soft_sync_window_ms", 400)
    config.setdefault("routing", copy.deepcopy(DEFAULT_ROUTING))
    return config


def describe_bus(config: dict[str, Any]) -> dict[str, Any]:
    """输出不依赖 SchemaSet 的总线摘要，供感知帧与编辑器面板展示。"""
    return {
        "sync_window_ms": config.get("sync_window_ms"),
        "soft_sync_window_ms": config.get("soft_sync_window_ms", 400),
        "tick_hz": config.get("tick_hz"),
        "channels": {
            channel: {
                "enabled": conf.get("enabled", True),
                "sync_mode": conf.get("sync_mode", "hard"),
                "devices": [
                    {
                        "id": device.get("id"),
                        "enabled": device.get("enabled", True),
                        "latency_ms": device.get("latency_ms"),
                        "capabilities": list(device.get("capabilities") or []),
                    }
                    for device in conf.get("devices") or []
                ],
            }
            for channel, conf in (config.get("channels") or {}).items()
        },
        "psychophysics": copy.deepcopy(config.get("psychophysics") or {}),
        "focus_policy": copy.deepcopy(config.get("focus_policy") or {}),
        "budget": copy.deepcopy(config.get("budget") or {}),
        "routing": copy.deepcopy(config.get("routing") or {}),
    }


def _perceive(intensity: float, curve: dict[str, Any]) -> float:
    value = _clamp(float(intensity), 0.0, 1.0)
    law = curve.get("law", "stevens")
    exponent = float(curve.get("exponent", 1.0))
    if law == "linear":
        return value
    if law == "fechner":
        return _clamp(math.log1p(9.0 * value) / math.log(10.0), 0.0, 1.0)
    return _clamp(value**exponent, 0.0, 1.0)


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(event)
    data.setdefault("duration_ms", 0)
    data.setdefault("priority", 0.5)
    data.setdefault("focus", True)
    return data


def status_effects_as_events(effects: Iterable[dict[str, Any]], t_ms: int = 0) -> list[dict[str, Any]]:
    """把 player_body 的状态效果转成统一感知事件，避免每个模块各写一套外设调用。"""
    events: list[dict[str, Any]] = []
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        effect_id = effect.get("id")
        if not isinstance(effect_id, str) or not effect_id:
            continue
        feedback = effect.get("feedback") or {}
        channels: dict[str, dict[str, Any]] = {}
        if feedback.get("vfx"):
            channels["visual"] = {"vfx": feedback["vfx"]}
        if feedback.get("sfx"):
            channels["audio"] = {"cue": feedback["sfx"]}
        if feedback.get("haptic"):
            channels["haptic"] = {"event": feedback["haptic"]}
        if feedback.get("scent"):
            channels["scent"] = {"scent_id": feedback["scent"]}
        if feedback.get("taste"):
            channels["taste"] = {"food_id": feedback["taste"]}
        if not channels:
            continue
        events.append(
            {
                "schema_version": "0.1.0",
                "id": f"status_{effect_id}",
                "kind": "status",
                "t_ms": int(t_ms),
                "duration_ms": 0,
                "intensity": _clamp(float(effect.get("threshold", 0.5)), 0.0, 1.0),
                "priority": 0.8,
                "focus": True,
                "source": {"type": "player", "id": "player"},
                "channels": channels,
            }
        )
    return events


def _align(
    group: list[dict[str, Any]], window_ms: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, float]:
    """把一组通道对齐到同一到达时刻；时间窗不足时裁剪最慢的通道。"""
    if not group:
        return [], [], 0.0, 0.0

    latencies = {
        item["channel"]: max(float(device["latency_ms"]) for device in item["devices"])
        for item in group
    }
    kept = list(group)
    dropped: list[dict[str, Any]] = []
    while len(kept) > 1:
        values = [latencies[item["channel"]] for item in kept]
        if max(values) - min(values) <= window_ms:
            break
        slowest = max(kept, key=lambda item: latencies[item["channel"]])
        kept.remove(slowest)
        dropped.append(
            {
                "channel": slowest["channel"],
                "reason": "sync_window",
                "detail": f"通道时延 {latencies[slowest['channel']]:g}ms 超出 {window_ms:g}ms 同步窗",
            }
        )

    values = [latencies[item["channel"]] for item in kept]
    target = max(values)
    skew = max(values) - min(values)
    for item in kept:
        item["target_latency_ms"] = target
    return kept, dropped, skew, target


class SensoryEventBus:
    """一个世界包内的感知事件调度器。"""

    def __init__(self, config: dict[str, Any] | None = None, schemas: SchemaSet | None = None) -> None:
        self.config = copy.deepcopy(config) if isinstance(config, dict) else default_bus_config()
        self.config.setdefault("soft_sync_window_ms", 400)
        self.config.setdefault("routing", copy.deepcopy(DEFAULT_ROUTING))
        self._schemas = schemas
        self._validate_config()

    @classmethod
    def from_session(cls, session: Any, schemas: SchemaSet | None = None) -> "SensoryEventBus":
        return cls(bus_config(session), schemas=schemas)

    # ---------- 配置 ----------

    def _validate_config(self) -> None:
        schemas = self._ensure_schemas()
        errors = [
            {"path": pointer, "message": message}
            for pointer, message in schemas.iter_errors(self.config, BUS_SCHEMA)
        ]
        if not errors:
            soft = float(self.config.get("soft_sync_window_ms", 400))
            hard = float(self.config.get("sync_window_ms", 45))
            if soft < hard:
                errors.append(
                    {
                        "path": "soft_sync_window_ms",
                        "message": f"软同步窗口 {soft:g}ms 不能小于硬同步窗口 {hard:g}ms",
                    }
                )
        if errors:
            raise SensoryBusError("感官总线配置非法", errors)

    def _ensure_schemas(self) -> SchemaSet:
        if self._schemas is None:
            self._schemas = SchemaSet()
        return self._schemas

    def describe(self) -> dict[str, Any]:
        return describe_bus(self.config)

    # ---------- 计划 ----------

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        return self.plan([event])

    def plan(self, events: Iterable[dict[str, Any]]) -> dict[str, Any]:
        schemas = self._ensure_schemas()
        raw_events = [item for item in events]

        rejected: list[dict[str, Any]] = []
        normalized: list[tuple[int, dict[str, Any]]] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(raw_events):
            if not isinstance(raw, dict):
                rejected.append({"event_id": f"#{index}", "errors": [{"path": "/", "message": "事件必须是 JSON 对象"}]})
                continue
            event = _normalize_event(raw)
            errors = schemas.iter_errors(event, EVENT_SCHEMA)
            if errors:
                rejected.append(
                    {
                        "event_id": str(event.get("id", f"#{index}")),
                        "errors": [{"path": pointer, "message": message} for pointer, message in errors],
                    }
                )
                continue
            if event["id"] in seen_ids:
                rejected.append(
                    {
                        "event_id": event["id"],
                        "errors": [{"path": "/id", "message": "同一计划内事件 id 重复"}],
                    }
                )
                continue
            seen_ids.add(event["id"])
            normalized.append((index, event))

        tick_ms = max(1, round(1000.0 / float(self.config["tick_hz"])))
        max_per_tick = int(self.config["budget"]["max_events_per_tick"])
        buckets: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        for order, event in normalized:
            buckets.setdefault(int(event["t_ms"]) // tick_ms, []).append((order, event))

        budget_dropped: dict[int, dict[str, Any]] = {}
        for bucket in buckets.values():
            ranked = sorted(
                bucket,
                key=lambda item: (-float(item[1]["priority"]), int(item[1]["t_ms"]), item[0]),
            )
            budget_dropped.update({order: event for order, event in ranked[max_per_tick:]})

        plans: list[dict[str, Any]] = []
        for order, event in normalized:
            if order in budget_dropped:
                plans.append(
                    self._empty_plan(
                        event,
                        [{"channel": None, "reason": "frame_budget", "detail": f"单帧事件数超过 {max_per_tick}"}],
                    )
                )
            else:
                plans.append(self._plan_event(event))

        self._apply_channel_budget(plans)

        deliveries = [delivery for plan in plans for delivery in plan["deliveries"]]
        deliveries.sort(key=lambda item: (item["at_ms"], CHANNELS.index(item["channel"]), item["device"]))
        dropped = [drop for plan in plans for drop in plan["dropped"]]

        channel_usage = {
            channel: {
                "deliveries": sum(1 for item in deliveries if item["channel"] == channel),
                "dropped": sum(1 for item in dropped if item.get("channel") == channel),
            }
            for channel in CHANNELS
        }
        hard_skews = [
            float(plan["sync"]["skew_ms"])
            for plan in plans
            if plan["sync"].get("group") in ("hard", "mixed")
        ]

        return {
            "ok": not rejected,
            "sync_window_ms": self.config["sync_window_ms"],
            "soft_sync_window_ms": self.config.get("soft_sync_window_ms", 400),
            "tick_hz": self.config["tick_hz"],
            "events": plans,
            "deliveries": deliveries,
            "dropped": dropped,
            "rejected": rejected,
            "diagnostics": {
                "events_in": len(raw_events),
                "events_planned": len(plans),
                "events_rejected": len(rejected),
                "deliveries": len(deliveries),
                "dropped": len(dropped),
                "sync_violations": sum(1 for plan in plans if plan["sync"].get("status") == "degraded"),
                "max_hard_skew_ms": round(max(hard_skews, default=0.0), 3),
                "focus_culled": sum(1 for item in dropped if item.get("reason") == "focus_culled"),
                "budget_culled": sum(
                    1 for item in dropped if item.get("reason") in ("frame_budget", "channel_budget")
                ),
                "channels": channel_usage,
            },
        }

    # ---------- 单个事件 ----------

    def _select_channels(
        self, event: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        payloads = event["channels"]
        allowed = (self.config.get("routing") or {}).get(event["kind"])
        selected: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []

        for channel in CHANNELS:
            if channel not in payloads:
                continue
            conf = self.config["channels"][channel]
            if allowed is not None and channel not in allowed:
                dropped.append(
                    {"channel": channel, "reason": "route_denied", "detail": f"{event['kind']} 不允许 {channel}"}
                )
                continue
            if not conf.get("enabled", True):
                dropped.append({"channel": channel, "reason": "channel_disabled", "detail": f"{channel} 通道已关闭"})
                continue
            if float(event["priority"]) < float(conf.get("min_priority", 0.0)):
                dropped.append(
                    {
                        "channel": channel,
                        "reason": "priority_floor",
                        "detail": f"优先级 {event['priority']} 低于 {channel} 通道下限",
                    }
                )
                continue
            devices = [device for device in conf.get("devices") or [] if device.get("enabled", True)]
            if not devices:
                dropped.append({"channel": channel, "reason": "no_device", "detail": f"{channel} 通道无可用设备"})
                continue
            selected.append({"channel": channel, "payload": copy.deepcopy(payloads[channel]), "devices": devices})
        return selected, dropped

    def _plan_event(self, event: dict[str, Any]) -> dict[str, Any]:
        selected, dropped = self._select_channels(event)
        hard = [item for item in selected if self.config["channels"][item["channel"]]["sync_mode"] == "hard"]
        soft = [item for item in selected if self.config["channels"][item["channel"]]["sync_mode"] == "soft"]

        hard_kept, hard_dropped, hard_skew, _ = _align(hard, float(self.config["sync_window_ms"]))
        soft_kept, soft_dropped, _, _ = _align(soft, float(self.config.get("soft_sync_window_ms", 400)))
        dropped.extend(hard_dropped)
        dropped.extend(soft_dropped)

        policy = self.config.get("focus_policy") or {}
        attenuated = set(policy.get("attenuated_channels") or [])
        focus_enabled = bool(policy.get("enabled", False))
        drop_below = float(policy.get("drop_below", 0.05))
        focus_gain = 1.0
        if focus_enabled and not bool(event.get("focus", True)) and attenuated:
            focus_gain = float(policy.get("off_focus_scale", 1.0))

        materials: list[dict[str, Any]] = []
        for item in (*hard_kept, *soft_kept):
            channel = item["channel"]
            payload = item["payload"]
            gain = payload.get("gain", 1.0) if isinstance(payload, dict) else 1.0
            raw_intensity = _clamp(float(event["intensity"]) * float(gain), 0.0, 1.0)
            channel_focus = focus_gain if channel in attenuated else 1.0
            requested = _clamp(raw_intensity * channel_focus, 0.0, 1.0)
            perceived = _perceive(requested, self.config["psychophysics"][channel])

            if focus_enabled and not bool(event.get("focus", True)) and channel in attenuated and perceived < drop_below:
                dropped.append(
                    {
                        "channel": channel,
                        "reason": "focus_culled",
                        "detail": f"非焦点主观强度 {perceived:.3f} 低于 {drop_below:g}，按注意力策略忽略",
                    }
                )
                continue

            materials.append(
                {
                    "channel": channel,
                    "payload": payload,
                    "devices": item["devices"],
                    "target_latency_ms": item.get("target_latency_ms", 0.0),
                    "raw_intensity": raw_intensity,
                    "focus_gain": channel_focus,
                    "requested": requested,
                    "perceived": perceived,
                }
            )

        deliveries: list[dict[str, Any]] = []
        for material in materials:
            arrival_ms = round(float(event["t_ms"]) + float(material["target_latency_ms"]), 3)
            for device in material["devices"]:
                dispatch_at_ms = round(arrival_ms - float(device["latency_ms"]), 3)
                deliveries.append(
                    {
                        "event_id": event["id"],
                        "kind": event["kind"],
                        "channel": material["channel"],
                        "device": device["id"],
                        "at_ms": arrival_ms,
                        "dispatch_at_ms": dispatch_at_ms,
                        "offset_ms": round(dispatch_at_ms - float(event["t_ms"]), 3),
                        "intensity": round(material["raw_intensity"], 4),
                        "focus_gain": round(material["focus_gain"], 4),
                        "requested_intensity": round(material["requested"], 4),
                        "perceived_intensity": round(material["perceived"], 4),
                        "priority": float(event["priority"]),
                        "payload": copy.deepcopy(material["payload"]),
                        "capabilities": list(device.get("capabilities") or []),
                    }
                )

        if hard and soft:
            group = "mixed"
        elif hard:
            group = "hard"
        elif soft:
            group = "soft"
        else:
            group = "none"
        status = "degraded" if hard_dropped else "aligned"

        return {
            "event_id": event["id"],
            "kind": event["kind"],
            "t_ms": int(event["t_ms"]),
            "intensity": float(event["intensity"]),
            "priority": float(event["priority"]),
            "focus": bool(event.get("focus", True)),
            "sync": {
                "group": group,
                "window_ms": self.config["sync_window_ms"]
                if group in ("hard", "mixed")
                else self.config.get("soft_sync_window_ms", 400),
                "skew_ms": round(hard_skew if group in ("hard", "mixed") else 0.0, 3),
                "status": status,
            },
            "deliveries": deliveries,
            "dropped": dropped,
        }

    def _empty_plan(self, event: dict[str, Any], dropped: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "event_id": event["id"],
            "kind": event["kind"],
            "t_ms": int(event["t_ms"]),
            "intensity": float(event["intensity"]),
            "priority": float(event["priority"]),
            "focus": bool(event.get("focus", True)),
            "sync": {
                "group": "none",
                "window_ms": self.config["sync_window_ms"],
                "skew_ms": 0.0,
                "status": "aligned",
            },
            "deliveries": [],
            "dropped": dropped,
        }

    def _apply_channel_budget(self, plans: list[dict[str, Any]]) -> None:
        limits = self.config["budget"]["per_channel_events_per_sec"]
        accepted: dict[str, list[tuple[float, str]]] = {channel: [] for channel in CHANNELS}
        candidates = [
            (plan, delivery)
            for plan in plans
            for delivery in plan["deliveries"]
        ]
        candidates.sort(key=lambda item: (item[1]["at_ms"], -item[1]["priority"], CHANNELS.index(item[1]["channel"])))

        for plan, delivery in candidates:
            channel = delivery["channel"]
            at_ms = float(delivery["at_ms"])
            limit = int(limits[channel])
            # 配额按“事件”而非“设备派发条数”计算，多设备通道不会重复消耗预算。
            recent = [(stamp, event_id) for stamp, event_id in accepted[channel] if at_ms - stamp < 1000.0]
            same_event = any(event_id == delivery["event_id"] for _, event_id in recent)
            if not same_event and len({event_id for _, event_id in recent}) >= limit:
                plan["deliveries"].remove(delivery)
                plan["dropped"].append(
                    {
                        "channel": channel,
                        "reason": "channel_budget",
                        "detail": f"{channel} 通道 1 秒内已派发 {limit} 条事件，超出预算",
                    }
                )
                continue
            accepted[channel].append((at_ms, delivery["event_id"]))


def build_sensory_plan(
    session: Any,
    events: Iterable[dict[str, Any]] | None = None,
    *,
    include_status_effects: bool = False,
    t_ms: int = 0,
) -> dict[str, Any]:
    """从世界包构建一次感知计划；可选把玩家生理状态自动转成事件。"""
    merged = list(events or [])
    if include_status_effects:
        effects = (session.modules.get("player_body") or {}).get("status_effects") or []
        merged.extend(status_effects_as_events(effects, t_ms=t_ms))
    return SensoryEventBus.from_session(session).plan(merged)
