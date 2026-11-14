"""M004-05 生存策略层：在场景资源允许时自动进食、饮水。

它回答的是「环境能不能养活人」而不是「玩家想不想吃」：只要所在场景有水源和
食物供给，饥渴低于阈值时就地补给。用于无头运行时区分「被环境杀死」与
「玩家不作为」，也供 AI 角色代理复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _normalized(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


def _rate(value: Any, default: float) -> float:
    try:
        return min(10.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SurvivalPolicy:
    """自动进食/饮水策略；字段可由 player_body.survival 覆盖。"""

    auto_eat: bool = True
    auto_drink: bool = True
    eat_threshold: float = 0.35
    drink_threshold: float = 0.4
    eat_rate_per_hour: float = 0.3
    drink_rate_per_hour: float = 0.6
    min_water_availability: float = 0.3
    min_food_biomass: float = 0.4
    min_built_density: float = 0.3

    @classmethod
    def from_module(cls, module: dict[str, Any] | None) -> SurvivalPolicy:
        config = (module or {}).get("survival") or {}
        defaults = cls()
        return cls(
            auto_eat=bool(config.get("auto_eat", defaults.auto_eat)),
            auto_drink=bool(config.get("auto_drink", defaults.auto_drink)),
            eat_threshold=_normalized(config.get("eat_threshold"), defaults.eat_threshold),
            drink_threshold=_normalized(config.get("drink_threshold"), defaults.drink_threshold),
            eat_rate_per_hour=_rate(config.get("eat_rate_per_hour"), defaults.eat_rate_per_hour),
            drink_rate_per_hour=_rate(config.get("drink_rate_per_hour"), defaults.drink_rate_per_hour),
        )


def survival_sources(uef: dict[str, Any] | None, policy: SurvivalPolicy) -> dict[str, bool]:
    """按场景 UEF 判断是否有可饮水源与食物供给。"""
    resources = (uef or {}).get("resources") or {}
    society = (uef or {}).get("society") or {}
    water = float(resources.get("water_availability", 0.0)) >= policy.min_water_availability
    food = (
        float(resources.get("biomass", 0.0)) >= policy.min_food_biomass
        or float(society.get("built_density", 0.0)) >= policy.min_built_density
    )
    return {"water": water, "food": food}


def apply_survival(
    player: Any,
    uef: dict[str, Any] | None,
    hours: float,
    policy: SurvivalPolicy,
) -> dict[str, bool]:
    """在场景资源允许时补给饥渴，返回本步是否进食/饮水及资源可得性。"""
    sources = survival_sources(uef, policy)
    drank = policy.auto_drink and sources["water"] and player.thirst < policy.drink_threshold
    if drank:
        player.thirst = min(1.0, player.thirst + policy.drink_rate_per_hour * hours)
    ate = policy.auto_eat and sources["food"] and player.hunger < policy.eat_threshold
    if ate:
        player.hunger = min(1.0, player.hunger + policy.eat_rate_per_hour * hours)
    return {"ate": ate, "drank": drank, "food": sources["food"], "water": sources["water"]}
