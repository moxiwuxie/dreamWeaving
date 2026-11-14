"""M004 最小无头运行时：用配置驱动一个可复现的 tick 循环。

模拟六件事：天气、环境温度、玩家生理、场景介质、NPC 日程与生态种群。
它不依赖引擎，用于接入 UE 之前验证数值设计是否成立（会不会一小时就冻死、
水下氧气够不够、暴雨时 NPC 是否知道回家）。状态可序列化，支持中断续跑。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from .npc_schedule import schedule_at
from .survival import SurvivalPolicy, apply_survival
from .world import WorldSession

STATE_VERSION = 1
PLAYER_FIELDS = ("core_temperature_c", "hunger", "thirst", "fatigue", "oxygen_seconds", "health")

MINUTES_PER_DAY = 1440
DIURNAL_AMPLITUDE_C = 5.0
CORE_TEMP_RESPONSE_PER_HOUR = 0.08
CLOTHING_INSULATION = 0.5
WEATHER_ROLL_MINUTES = 360
SLEEP_START_MINUTE = 22 * 60
SLEEP_END_MINUTE = 6 * 60

# 户外活动遇到灾害性天气会中断日程，回到常驻场景避险。
SHELTER_ACTIVITIES = frozenset({"patrol", "work", "socialize", "commute", "leisure", "shop", "worship"})
WEATHER_SHELTER_INTENSITY = {
    "rain": 0.5,
    "snow": 0.5,
    "storm": 0.5,
    "sandstorm": 0.5,
    "hurricane": 0.3,
    "coldwave": 0.6,
    "heatwave": 0.7,
    "fog": 0.85,
}

# 相对种群指数：1.0 代表当前生态位的基准规模，只比较趋势，不代表绝对数量。
ECOLOGY_GROWTH_PER_DAY = {"plant": 0.10, "herbivore": 0.05, "carnivore": 0.02}
ECOLOGY_WEATHER_STRESS = {
    "heatwave": 0.15,
    "coldwave": 0.12,
    "hurricane": 0.2,
    "sandstorm": 0.1,
    "snow": 0.08,
    "storm": 0.06,
    "fog": 0.02,
}
ECOLOGY_POPULATION_MAX = 1.5
ECOLOGY_EXTINCT_THRESHOLD = 0.02
ECOLOGY_DECLINING_THRESHOLD = 0.5

WEATHER_TEMPERATURE_DELTA_C = {
    "rain": -1.5,
    "snow": -5.0,
    "storm": -2.0,
    "fog": -0.5,
    "heatwave": 8.0,
    "coldwave": -8.0,
    "sandstorm": 3.0,
    "hurricane": -3.0,
    "aurora": 0.0,
}

WATER_MEDIA = ("water", "seawater")


def weather_roll_probability(probability_per_day: float, minutes: int) -> float:
    """把「每天概率」换算为一次掷骰概率，避免每 6 小时重复掷骰造成概率膨胀。"""
    if probability_per_day <= 0:
        return 0.0
    if probability_per_day >= 1:
        return 1.0
    return 1.0 - (1.0 - probability_per_day) ** (max(minutes, 0) / MINUTES_PER_DAY)


def _encode_rng_state(state: tuple[Any, ...]) -> dict[str, Any]:
    version, internal, gauss = state
    return {"version": version, "internal": list(internal), "gauss": gauss}


def _decode_rng_state(data: dict[str, Any]) -> tuple[Any, ...]:
    try:
        return (
            int(data["version"]),
            tuple(int(value) for value in data["internal"]),
            data.get("gauss"),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("存档中的随机数状态无效") from exc


@dataclass
class PlayerState:
    core_temperature_c: float
    hunger: float = 1.0
    thirst: float = 1.0
    fatigue: float = 0.0
    oxygen_seconds: float = 0.0
    health: float = 100.0

    def to_dict(self) -> dict[str, float]:
        return {
            "core_temperature_c": round(self.core_temperature_c, 2),
            "hunger": round(self.hunger, 3),
            "thirst": round(self.thirst, 3),
            "fatigue": round(self.fatigue, 3),
            "oxygen_seconds": round(self.oxygen_seconds, 1),
            "health": round(self.health, 2),
        }


class Simulation:
    """一个世界包的无头运行时；同一 seed 下结果完全可复现。"""

    def __init__(
        self,
        session: WorldSession,
        *,
        day: int = 1,
        minute: int = 0,
        position_m: list[float] | tuple[float, float, float] | None = None,
        seed: int = 7,
    ) -> None:
        self.session = session
        self.rng = random.Random(seed)
        self._absolute_minute = (day - 1) * MINUTES_PER_DAY + minute
        self.position_m = [float(value) for value in position_m] if position_m else None
        self.scene = session.locate_scene(self.position_m)["scene"] if self.position_m else None

        player = session.modules.get("player_body", {})
        vitals = player.get("vitals", {})
        self.temperature_cfg = vitals.get("temperature", {})
        self.hunger_cfg = vitals.get("hunger", {})
        self.thirst_cfg = vitals.get("thirst", {})
        self.fatigue_cfg = vitals.get("fatigue", {})
        self.oxygen_cfg = vitals.get("oxygen", {})
        self.status_effects = player.get("status_effects", [])
        self.survival_policy = SurvivalPolicy.from_module(player)
        self._survival: dict[str, bool] = {"ate": False, "drank": False, "food": False, "water": False}

        self.player = PlayerState(
            core_temperature_c=float(self.temperature_cfg.get("baseline_c", 36.8)),
            oxygen_seconds=float(self.oxygen_cfg.get("capacity_seconds", 90)),
        )
        self.weather: dict[str, Any] | None = None
        self._weather_until = self._absolute_minute
        self._last_weather_roll = self._absolute_minute
        self.events: list[dict[str, Any]] = []
        self._active_effects: set[str] = set()

        npc_data = session.modules.get("npc_agent", {}) or {}
        self._npc_roster = [
            item for item in npc_data.get("npc_roster", []) if isinstance(item, dict) and item.get("id")
        ]
        self.npcs: dict[str, dict[str, Any]] = {
            agent["id"]: {
                "npc_id": agent["id"],
                "name": agent.get("name"),
                "layer": agent.get("layer"),
                "activity": "idle",
                "location": None,
                "scene_id": agent.get("scene_id"),
                "home_scene_id": agent.get("scene_id"),
                "status": "idle",
            }
            for agent in self._npc_roster
        }
        self._sync_npcs(emit=False)

        ecology = session.modules.get("ecology", {}) or {}
        self._species = [item for item in ecology.get("species", []) if isinstance(item, dict) and item.get("id")]
        self.ecology: dict[str, dict[str, Any]] = {
            species["id"]: {
                "id": species["id"],
                "name": species.get("name"),
                "kingdom": species.get("kingdom"),
                "population": 1.0,
                "status": "stable",
            }
            for species in self._species
        }
        self._ecology_minutes = 0
        self._ecology_stress = 0.0

    # ---------- 时钟与环境 ----------

    @property
    def day(self) -> int:
        return self._absolute_minute // MINUTES_PER_DAY + 1

    @property
    def absolute_minute(self) -> int:
        """自世界第 1 天 0 点起的绝对分钟数，用于存档衔接与推进窗口计算。"""
        return self._absolute_minute

    def move_to(self, position_m: list[float] | tuple[float, ...]) -> dict[str, Any] | None:
        """把玩家移动到世界坐标，并重新解析所在场景。"""
        self.position_m = [float(value) for value in position_m]
        located = self.session.locate_scene(self.position_m)
        self.scene = located["scene"]
        return self.scene

    @property
    def minute(self) -> int:
        return self._absolute_minute % MINUTES_PER_DAY

    @property
    def asleep(self) -> bool:
        return self.minute >= SLEEP_START_MINUTE or self.minute < SLEEP_END_MINUTE

    def ambient_temperature_c(self) -> float:
        climate = self.session.modules.get("ecology", {}).get("climate", {})
        if self.scene is not None:
            base = float(self.scene["uef"]["energy"]["ambient_temperature_c"])
        else:
            base = float(climate.get("base_temperature_c", 15))
        amplitude = float(climate.get("seasonal_amplitude_c", 0))
        seasonal = amplitude * math.sin(2 * math.pi * (self.day - 80) / 365)
        diurnal = DIURNAL_AMPLITUDE_C * math.sin(2 * math.pi * (self.minute - 360) / MINUTES_PER_DAY)
        return base + seasonal + diurnal + self._weather_delta()

    def _weather_delta(self) -> float:
        if not self.weather:
            return 0.0
        delta = WEATHER_TEMPERATURE_DELTA_C.get(self.weather["kind"], 0.0)
        return delta * float(self.weather.get("intensity", 1.0))

    def _breathable(self) -> bool:
        if self.scene is None:
            return True
        uef = self.scene["uef"]
        medium = uef["medium"]["kind"]
        if medium in WATER_MEDIA or medium in ("vacuum", "plasma"):
            return False
        return float(uef["resources"]["breathable_oxygen"]) >= 0.5

    # ---------- 存档与续跑 ----------

    def to_state(self) -> dict[str, Any]:
        """导出完整运行状态；配合 from_state 可跨进程续跑。"""
        return {
            "state_version": STATE_VERSION,
            "absolute_minute": self._absolute_minute,
            "position_m": self.position_m,
            "weather": self.weather,
            "weather_until": self._weather_until,
            "last_weather_roll": self._last_weather_roll,
            "player": {field: getattr(self.player, field) for field in PLAYER_FIELDS},
            "events": list(self.events),
            "active_effects": sorted(self._active_effects),
            "survival": dict(self._survival),
            "npcs": {key: dict(value) for key, value in self.npcs.items()},
            "ecology": {key: dict(value) for key, value in self.ecology.items()},
            "ecology_minutes": self._ecology_minutes,
            "ecology_stress": self._ecology_stress,
            "rng_state": _encode_rng_state(self.rng.getstate()),
        }

    @classmethod
    def from_state(
        cls,
        session: WorldSession,
        state: dict[str, Any],
        *,
        seed: int = 7,
    ) -> Simulation:
        """从 to_state 的产物恢复运行时；恢复后继续 tick 与不中断运行结果一致。"""
        if int(state.get("state_version", 0)) != STATE_VERSION:
            raise ValueError(
                f"不支持的存档版本：{state.get('state_version')}，当前为 {STATE_VERSION}"
            )
        try:
            absolute_minute = int(state["absolute_minute"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("存档缺少有效的 absolute_minute") from exc
        if absolute_minute < 0:
            raise ValueError("存档的 absolute_minute 不能为负")

        position = state.get("position_m")
        if position is not None and (not isinstance(position, list) or len(position) != 3):
            raise ValueError("存档中的 position_m 必须是 3 个数字")

        simulation = cls(
            session,
            day=absolute_minute // MINUTES_PER_DAY + 1,
            minute=absolute_minute % MINUTES_PER_DAY,
            position_m=position,
            seed=seed,
        )
        simulation._absolute_minute = absolute_minute
        simulation.weather = state.get("weather")
        simulation._weather_until = int(state.get("weather_until", absolute_minute))
        simulation._last_weather_roll = int(state.get("last_weather_roll", absolute_minute))

        raw_player = state.get("player") or {}
        missing = [field for field in PLAYER_FIELDS if field not in raw_player]
        if missing:
            raise ValueError(f"存档缺少玩家字段：{'、'.join(missing)}")
        simulation.player = PlayerState(**{field: float(raw_player[field]) for field in PLAYER_FIELDS})

        simulation.events = [dict(item) for item in state.get("events", [])]
        simulation._active_effects = set(state.get("active_effects", []))
        for key, value in (state.get("survival") or {}).items():
            if key in simulation._survival:
                simulation._survival[key] = bool(value)
        simulation.npcs = {
            str(key): dict(value) for key, value in (state.get("npcs") or {}).items()
        }
        simulation.ecology = {
            str(key): dict(value) for key, value in (state.get("ecology") or {}).items()
        }
        simulation._ecology_minutes = int(state.get("ecology_minutes", 0))
        simulation._ecology_stress = float(state.get("ecology_stress", 0.0))
        rng_state = state.get("rng_state")
        if rng_state is not None:
            simulation.rng.setstate(_decode_rng_state(rng_state))
        return simulation

    # ---------- 主循环 ----------

    def tick(self, minutes: int = 60, step: int = 10) -> dict[str, Any]:
        if minutes <= 0 or step <= 0:
            raise ValueError("minutes 与 step 必须为正整数")
        remaining = minutes
        while remaining > 0:
            span = min(step, remaining)
            self._advance(span)
            remaining -= span
        return self.snapshot()

    def _advance(self, minutes: int) -> None:
        self._absolute_minute += minutes
        hours = minutes / 60.0
        self._roll_weather()
        self._sync_npcs()
        self._update_ecology(minutes)
        self._update_temperature(hours)
        self._update_vitals(minutes, hours)
        self._update_effects()

    # ---------- NPC 日程 ----------

    def _shelter_active(self) -> bool:
        if not self.weather:
            return False
        threshold = WEATHER_SHELTER_INTENSITY.get(self.weather["kind"])
        return threshold is not None and float(self.weather.get("intensity", 1.0)) >= threshold

    def _sync_npcs(self, *, emit: bool = True) -> None:
        """把 NPC 对齐到当前时刻的日程；灾害天气时中断户外活动并回常驻场景。"""
        if not self.npcs:
            return
        try:
            scheduled = {
                item["npc_id"]: item
                for item in schedule_at(self.session, day=self.day, minute=self.minute)
            }
        except ValueError:
            return

        shelter = self._shelter_active()
        for npc_id, state in self.npcs.items():
            item = scheduled.get(npc_id)
            if item is None:
                continue
            activity = item["activity"]
            location = item["location"]
            scene_id = item["scene_id"]
            status = "scheduled"
            if shelter and activity in SHELTER_ACTIVITIES:
                activity = "shelter"
                location = "home"
                scene_id = state["home_scene_id"] or scene_id
                status = "sheltered"

            if scene_id != state["scene_id"]:
                if emit:
                    self.events.append(
                        {
                            "minute": self._absolute_minute,
                            "type": "npc_move",
                            "value": f"{npc_id} {state['scene_id'] or '无场景'}→{scene_id or '无场景'}",
                        }
                    )
                state["scene_id"] = scene_id
            if activity != state["activity"]:
                if emit:
                    self.events.append(
                        {
                            "minute": self._absolute_minute,
                            "type": "npc_activity",
                            "value": f"{npc_id} {activity}",
                        }
                    )
                state["activity"] = activity
            state["location"] = location
            state["status"] = status

    # ---------- 生态 ----------

    def _weather_stress(self) -> float:
        if not self.weather:
            return 0.0
        return ECOLOGY_WEATHER_STRESS.get(self.weather["kind"], 0.0) * float(self.weather.get("intensity", 1.0))

    def _update_ecology(self, minutes: int) -> None:
        if not self.ecology:
            return
        remaining = minutes
        while remaining > 0:
            span = min(remaining, MINUTES_PER_DAY - self._ecology_minutes)
            self._ecology_stress += self._weather_stress() * span
            self._ecology_minutes += span
            remaining -= span
            if self._ecology_minutes >= MINUTES_PER_DAY:
                self._step_ecology()
                self._ecology_minutes = 0
                self._ecology_stress = 0.0

    def _step_ecology(self) -> None:
        """每日一步的相对种群演替：植物受天气压制，草食与肉食沿食物网传导。"""
        stress = self._ecology_stress / MINUTES_PER_DAY
        for species in self._species:
            state = self.ecology[species["id"]]
            kingdom = state["kingdom"]
            growth = ECOLOGY_GROWTH_PER_DAY.get(kingdom, 0.03)
            population = state["population"]
            prey = (species.get("food_web") or {}).get("prey") or []
            foods = [self.ecology[item]["population"] for item in prey if item in self.ecology]
            food = min(foods) if foods else 1.0

            drift = growth * population * (1.0 - population) + 0.5 * growth * population * (food - 1.0)
            if kingdom == "plant":
                drift -= population * stress
            elif kingdom == "herbivore":
                drift -= population * stress * 0.3
            state["population"] = min(ECOLOGY_POPULATION_MAX, max(0.0, population + drift))

            population = state["population"]
            status = (
                "extinct"
                if population <= ECOLOGY_EXTINCT_THRESHOLD
                else ("declining" if population < ECOLOGY_DECLINING_THRESHOLD else "stable")
            )
            if status != state["status"]:
                self.events.append(
                    {
                        "minute": self._absolute_minute,
                        "type": "ecology",
                        "value": f"{state['id']} {state['status']}→{status}",
                    }
                )
                state["status"] = status

    def _roll_weather(self) -> None:
        if self._absolute_minute < self._weather_until:
            return
        elapsed = max(1, self._absolute_minute - self._last_weather_roll)
        self._last_weather_roll = self._absolute_minute
        climate = self.session.modules.get("ecology", {}).get("climate", {})
        chosen: dict[str, Any] | None = None
        for event in climate.get("weather_events", []):
            probability = weather_roll_probability(
                float(event.get("probability_per_day", 0)), elapsed
            )
            if self.rng.random() < probability:
                if chosen is None or event.get("intensity", 0) > chosen.get("intensity", 0):
                    chosen = event

        previous_id = (self.weather or {}).get("id")
        if chosen is None:
            self.weather = None
            self._weather_until = self._absolute_minute + WEATHER_ROLL_MINUTES
        else:
            self.weather = {
                "id": chosen.get("id"),
                "kind": chosen.get("kind"),
                "intensity": chosen.get("intensity", 1.0),
            }
            duration = float(chosen.get("duration_min", WEATHER_ROLL_MINUTES))
            self._weather_until = self._absolute_minute + max(duration, 1)
        if (self.weather or {}).get("id") != previous_id:
            self.events.append(
                {
                    "minute": self._absolute_minute,
                    "type": "weather",
                    "value": (self.weather or {}).get("kind", "clear"),
                }
            )

    def _update_temperature(self, hours: float) -> None:
        ambient = self.ambient_temperature_c()
        baseline = float(self.temperature_cfg.get("baseline_c", 36.8))
        comfort = self.temperature_cfg.get("comfort_ambient_range_c") or [10, 30]

        if comfort[0] <= ambient <= comfort[1]:
            # 环境舒适时由体温调节把核心温度拉回基准，不向环境漂移。
            target, rate = baseline, min(1.0, 0.6 * hours)
        else:
            conductivity = float(
                self.session.modules.get("physics", {}).get("thermal", {}).get("conductivity_scale", 1.0)
            )
            target = ambient
            rate = min(1.0, CORE_TEMP_RESPONSE_PER_HOUR * CLOTHING_INSULATION * conductivity * hours)
        self.player.core_temperature_c += (target - self.player.core_temperature_c) * rate
        self.player.core_temperature_c = min(60.0, max(-20.0, self.player.core_temperature_c))

        low = float(self.temperature_cfg.get("critical_low_c", 28))
        high = float(self.temperature_cfg.get("critical_high_c", 42))
        if self.player.core_temperature_c < low:
            severity = (low - self.player.core_temperature_c) / max(1.0, baseline - low)
            self.player.health -= severity * 8 * hours
        elif self.player.core_temperature_c > high:
            severity = (self.player.core_temperature_c - high) / max(1.0, high - baseline)
            self.player.health -= severity * 8 * hours

    def _update_vitals(self, minutes: int, hours: float) -> None:
        self.player.hunger = max(0.0, self.player.hunger - float(self.hunger_cfg.get("drain_per_hour", 0)) * hours)
        self.player.thirst = max(0.0, self.player.thirst - float(self.thirst_cfg.get("drain_per_hour", 0)) * hours)

        if self.asleep:
            self.player.fatigue = max(
                0.0, self.player.fatigue - float(self.fatigue_cfg.get("recovery_per_hour_asleep", 0)) * hours
            )
        else:
            self.player.fatigue = min(
                1.0, self.player.fatigue + float(self.fatigue_cfg.get("drain_per_hour_awake", 0)) * hours
            )

        self._update_survival(hours)

        if self.player.hunger <= float(self.hunger_cfg.get("starvation_threshold", 0)):
            self.player.health -= float(self.hunger_cfg.get("damage_per_hour", 0)) * hours
        if self.player.thirst <= float(self.thirst_cfg.get("dehydration_threshold", 0)):
            self.player.health -= float(self.thirst_cfg.get("damage_per_hour", 0)) * hours

        capacity = float(self.oxygen_cfg.get("capacity_seconds", 90))
        if self._breathable():
            regen = float(self.oxygen_cfg.get("regen_per_second", 0)) * minutes * 60
            self.player.oxygen_seconds = min(capacity, self.player.oxygen_seconds + regen)
        else:
            self.player.oxygen_seconds = max(0.0, self.player.oxygen_seconds - minutes * 60)
            if self.player.oxygen_seconds <= 0:
                self.player.health -= float(self.oxygen_cfg.get("drowning_damage_per_second", 0)) * minutes * 60

        self.player.health = min(100.0, max(0.0, self.player.health))

    def _update_survival(self, hours: float) -> None:
        """饥渴先自然消耗，再按场景资源自动补给，最后才判定伤害。"""
        uef = self.scene["uef"] if self.scene else None
        status = apply_survival(self.player, uef, hours, self.survival_policy)
        for key, label in (("drank", "饮水"), ("ate", "进食")):
            if status[key] != self._survival[key]:
                self.events.append(
                    {
                        "minute": self._absolute_minute,
                        "type": "survival",
                        "value": f"{label}{'开始' if status[key] else '结束'}",
                    }
                )
                self._survival[key] = status[key]
        self._survival["food"] = status["food"]
        self._survival["water"] = status["water"]

    def _update_effects(self) -> None:
        active = {effect["id"] for effect in self._effects()}
        for effect_id in sorted(active - self._active_effects):
            self.events.append({"minute": self._absolute_minute, "type": "effect_on", "value": effect_id})
        for effect_id in sorted(self._active_effects - active):
            self.events.append({"minute": self._absolute_minute, "type": "effect_off", "value": effect_id})
        self._active_effects = active

    def _effects(self) -> list[dict[str, Any]]:
        baseline = float(self.temperature_cfg.get("baseline_c", 36.8))
        low = float(self.temperature_cfg.get("critical_low_c", 28))
        high = float(self.temperature_cfg.get("critical_high_c", 42))
        core = self.player.core_temperature_c

        results = []
        for effect in self.status_effects:
            trigger = effect.get("trigger")
            threshold = float(effect.get("threshold", 0))
            severity = 0.0
            if trigger == "temperature_low" and baseline > low:
                severity = (baseline - core) / (baseline - low)
            elif trigger == "temperature_high" and high > baseline:
                severity = (core - baseline) / (high - baseline)
            elif trigger == "hunger_low":
                severity = 1.0 - self.player.hunger
                if self.player.hunger > threshold:
                    continue
            elif trigger == "thirst_low":
                severity = 1.0 - self.player.thirst
                if self.player.thirst > threshold:
                    continue
            elif trigger == "fatigue_high":
                severity = self.player.fatigue
                if self.player.fatigue < threshold:
                    continue
            elif trigger == "oxygen_low":
                capacity = float(self.oxygen_cfg.get("capacity_seconds", 90)) or 1.0
                severity = 1.0 - self.player.oxygen_seconds / capacity
                if severity < threshold:
                    continue
            elif trigger == "bleeding":
                continue
            if severity > threshold:
                results.append({**effect, "severity": round(min(1.0, severity), 3)})
        return results

    # ---------- 输出 ----------

    def snapshot(self) -> dict[str, Any]:
        return {
            "clock": {
                "day": self.day,
                "minute": self.minute,
                "time": f"{self.minute // 60:02d}:{self.minute % 60:02d}",
            },
            "position_m": self.position_m,
            "scene": (
                {"id": self.scene["id"], "name": self.scene["name"], "category": self.scene["category"]}
                if self.scene
                else None
            ),
            "ambient_temperature_c": round(self.ambient_temperature_c(), 2),
            "weather": self.weather,
            "player": self.player.to_dict(),
            "effects": self._effects(),
            "survival": dict(self._survival),
            "npcs": [dict(item) for item in sorted(self.npcs.values(), key=lambda x: (str(x["layer"]), str(x["npc_id"])))],
            "ecology": [
                {**self.ecology[species["id"]], "population": round(self.ecology[species["id"]]["population"], 3)}
                for species in self._species
            ],
        }
