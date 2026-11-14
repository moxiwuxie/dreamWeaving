"""织梦世界运行时容器：单个世界会话 + 模拟、交互与 3D 视图。

HTTP 层在 :mod:`dreamweave.server`，控制台页面在 console.html，
3D 客户端在 play3d/。运行时状态在交互后原子写入世界目录之外的
JSON 存档，重启后自动恢复。
"""

from __future__ import annotations

import json
import math
import threading
import time
import zlib
from pathlib import Path
from typing import Any

from .interaction import (
    NPC_ACTIONS,
    PROP_ACTIONS,
    apply_effects,
    dance_line,
    hit_line,
    npc_trust_label,
    observe_line,
    prop_response,
    scene_props,
    talk_line,
)
from .narrative import advance_narrative
from .quests import build_quest_beat, quest_list, quest_operations, travel_minutes
from .sensory_bus import SensoryEventBus, status_effects_as_events
from .simulation import Simulation
from .timeflow import MINUTES_PER_DAY, advance_perceptual
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

NPC_LAYER_COLORS = {
    "mass_life": "#7cc7e8",
    "llm_core": "#c4a0ff",
    "crowd": "#9fb0c4",
}


def _stable_seed(*parts: Any) -> int:
    """跨进程稳定的整型种子，用于程序化摆放 NPC/道具。"""
    return zlib.crc32("|".join(str(part) for part in parts).encode("utf-8")) & 0x7FFFFFFF


def _scene_palette(scene: dict[str, Any]) -> tuple[str, str]:
    """由 UEF 六轴推导场景地面色与强调色，不额外维护配色表。"""
    uef = scene.get("uef") or {}
    category = scene.get("category")
    medium = (uef.get("medium") or {}).get("kind")
    terrain = uef.get("terrain") or {}
    energy = uef.get("energy") or {}
    resources = uef.get("resources") or {}
    society = uef.get("society") or {}
    temperature = float(energy.get("ambient_temperature_c", 15))
    biomass = float(resources.get("biomass", 0.5))
    water = float(resources.get("water_availability", 0.5))
    relief = float(terrain.get("relief_m", 50))
    built = float(society.get("built_density", 0))
    if medium in ("water", "seawater"):
        return ("#1c4a63", "#4fb3d9")
    if category == "extraterrestrial":
        return ("#6a6670", "#c9c2b2")
    if temperature <= -5:
        return ("#dbe7f0", "#9fb8cc")
    if water <= 0.08:
        return ("#b8894f", "#efd08a")
    if category == "built" and built >= 0.5:
        return ("#4c5560", "#7cc7e8")
    if biomass >= 0.8:
        return ("#2f5c38", "#8fcf76")
    if relief >= 500:
        return ("#5a5852", "#cfd9e4")
    if category == "built":
        return ("#5c6b4f", "#b9c98c")
    if biomass <= 0.2:
        return ("#8a8578", "#d9cfae")
    return ("#3f7a46", "#a3d977")


SENSORY_PRESETS: dict[str, dict[str, Any]] = {
    "impact": {
        "intensity": 0.8,
        "priority": 0.9,
        "channels": {
            "visual": {"vfx": "vfx_impact_dust", "screen_shake": 0.3},
            "audio": {"cue": "sfx_stone_impact", "material": "stone", "layer": "near"},
            "haptic": {"event": "haptic_impact_heavy", "region": "hands"},
        },
    },
    "step": {
        "intensity": 0.35,
        "priority": 0.4,
        "channels": {
            "audio": {"cue": "sfx_footstep_grass", "material": "grass", "layer": "near"},
            "haptic": {"event": "haptic_step_light", "region": "hands"},
        },
    },
    "voice": {
        "intensity": 0.6,
        "priority": 0.6,
        "channels": {"audio": {"cue": "vo_npc_greeting", "layer": "near"}},
    },
    "scent": {
        "intensity": 0.7,
        "priority": 0.5,
        "channels": {"scent": {"scent_id": "scent_bread", "concentration": 0.6}},
    },
    "taste": {
        "intensity": 0.6,
        "priority": 0.5,
        "channels": {"taste": {"food_id": "food_bread", "basic": {"sweet": 0.2}}},
    },
}


class Playground:
    """单世界运行容器；所有读写通过锁串行化。"""

    def __init__(
        self,
        target: str,
        *,
        position_m: list[float] | tuple[float, float, float] | None = None,
        seed: int = 7,
        start_minute: int = 0,
        state_path: str | Path | None = None,
    ) -> None:
        self.lock = threading.Lock()
        self.session = WorldSession(target)
        self.state_path = Path(state_path) if state_path else self._default_state_path()
        self.seed = seed
        self.start_minute = max(0, int(start_minute))
        self.spawn = list(position_m) if position_m else self._default_spawn()
        self.simulation = self._new_simulation()
        self.bus = SensoryEventBus.from_session(self.session)
        self.last_sensory: dict[str, Any] | None = None
        self.completed: set[str] = set()
        self.actions: list[dict[str, Any]] = []
        self.recap: dict[str, Any] | None = None
        self.trust: dict[str, float] = {}
        self.talk_counts: dict[str, int] = {}
        self.interactions: list[dict[str, Any]] = []
        self.picked_props: set[str] = set()
        self.prop_notes: dict[str, str] = {}
        self.carried: dict[str, Any] | None = None
        self.props: list[dict[str, Any]] = []
        self._scene_cache: list[dict[str, Any]] | None = None
        self._roster = {
            item["id"]: item
            for item in (self.session.modules.get("npc_agent", {}) or {}).get("npc_roster", [])
            if isinstance(item, dict) and item.get("id")
        }
        for summary in self.session.query_scenes():
            self.props.extend(
                scene_props(summary["id"], summary["category"], summary["world_bounds_m"])
            )
        self._prop_spawn = {
            prop["id"]: list(prop["position_m"]) for prop in self.props
        }
        self._restore_state()

    def _new_simulation(self) -> Simulation:
        return Simulation(
            self.session,
            day=self.start_minute // 1440 + 1,
            minute=self.start_minute % 1440,
            position_m=self.spawn,
            seed=self.seed,
        )

    def _default_spawn(self) -> list[float] | None:
        try:
            return self.session.scene_spawn()
        except WorldError:
            return None

    def _default_state_path(self) -> Path:
        """运行时状态写在世界目录之外，避免污染世界包本身。"""
        manifest = self.session.manifest or {}
        world_id = str(manifest.get("world_id") or self.session.root.name or "world")
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in world_id)
        return self.session.root.parent / ".dreamweave" / "runtime" / f"{safe}.json"

    def _restore_state(self) -> None:
        """载入上次的运行状态；文件缺失或损坏时保持全新世界。"""
        path = self.state_path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        props = {prop["id"]: prop for prop in self.props}

        trust = raw.get("trust")
        if isinstance(trust, dict):
            for key, value in trust.items():
                if isinstance(key, str) and isinstance(value, (int, float)):
                    self.trust[key] = min(1.0, max(0.0, float(value)))

        talks = raw.get("talk_counts")
        if isinstance(talks, dict):
            for key, value in talks.items():
                if isinstance(key, str) and isinstance(value, int) and value >= 0:
                    self.talk_counts[key] = value

        interactions = raw.get("interactions")
        if isinstance(interactions, list):
            self.interactions = [
                item for item in interactions if isinstance(item, dict)
            ][-40:]

        picked = raw.get("picked_props")
        if isinstance(picked, list):
            self.picked_props = {
                str(item) for item in picked if str(item) in props
            }

        notes = raw.get("prop_notes")
        if isinstance(notes, dict):
            self.prop_notes = {
                str(key): str(value)[:200]
                for key, value in notes.items()
                if str(key) in props and isinstance(value, str)
            }

        positions = raw.get("prop_positions")
        if isinstance(positions, dict):
            for key, value in positions.items():
                prop = props.get(str(key))
                if prop is None or not isinstance(value, list) or len(value) != 3:
                    continue
                try:
                    spot = [float(value[0]), float(value[1]), float(value[2])]
                except (TypeError, ValueError):
                    continue
                if all(math.isfinite(axis) for axis in spot):
                    prop["position_m"] = spot

        held = raw.get("carried")
        if isinstance(held, dict):
            prop = props.get(str(held.get("id")))
            if prop is not None:
                self.carried = {
                    "id": prop["id"],
                    "kind": prop["kind"],
                    "name": prop["name"],
                    "scene_id": prop["scene_id"],
                }

    def _save_state(self) -> None:
        """原子写入运行状态；调用方需持有 self.lock。"""
        payload = {
            "version": 1,
            "world_id": (self.session.manifest or {}).get("world_id"),
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "trust": self.trust,
            "talk_counts": self.talk_counts,
            "interactions": self.interactions[-40:],
            "picked_props": sorted(self.picked_props),
            "prop_notes": self.prop_notes,
            "carried": self.carried,
            "prop_positions": {
                prop["id"]: list(prop["position_m"])
                for prop in self.props
                if list(prop["position_m"]) != self._prop_spawn.get(prop["id"])
            },
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp.replace(self.state_path)
        except OSError:
            pass

    def _clear_state(self) -> None:
        try:
            self.state_path.unlink()
        except OSError:
            pass

    def reset(self) -> None:
        with self.lock:
            self.simulation = self._new_simulation()
            self._invalidate_scenes()
            self.completed = set()
            self.actions = []
            self.recap = None
            self.last_sensory = None
            self.trust = {}
            self.talk_counts = {}
            self.interactions = []
            self.picked_props = set()
            self.prop_notes = {}
            self.carried = None
            for prop in self.props:
                spawn = self._prop_spawn.get(prop["id"])
                if spawn:
                    prop["position_m"] = list(spawn)
            self._clear_state()

    def tick(self, minutes: int, step: int) -> dict[str, Any]:
        with self.lock:
            before = len(self.simulation.events)
            self.simulation.tick(minutes, step)
            new_events = self.simulation.events[before:]
            return {
                "accepted": True,
                "message": f"推进 {minutes} 分钟",
                "events": new_events,
                "state": self._state(),
            }

    def timeflow(self, days: int, step: int = 60) -> dict[str, Any]:
        """压缩推进若干天，只采样可感知事件并返回记忆回放。"""
        with self.lock:
            minutes = max(1, int(days) * MINUTES_PER_DAY)
            recap = advance_perceptual(self.simulation, minutes, step=step)
            self.recap = recap
            return {
                "accepted": True,
                "message": recap["summary"],
                "recap": recap,
                "state": self._state(),
            }

    def advance(self, days: int) -> dict[str, Any]:
        with self.lock:
            result = advance_narrative(
                self.session,
                days=days,
                seed=self.seed,
                dry_run=False,
                state=self.simulation.to_state(),
            )
            if result["accepted"]:
                self.simulation = Simulation.from_state(
                    self.session, result["state"], seed=self.seed
                )
                self._invalidate_scenes()
            return {
                "accepted": result["accepted"],
                "message": result["message"],
                "changes": result["changes"],
                "chronicle": result["chronicle"],
                "errors": result["errors"],
                "state": self._state(),
            }

    def act(self, quest_id: str) -> dict[str, Any]:
        """前往任务场景、消耗旅途与任务时间，并把操作写成剧情节拍。"""
        with self.lock:
            quests = {
                item["id"]: item
                for item in self.session.modules.get("narrative", {}).get("quests", [])
                if isinstance(item, dict) and item.get("id")
            }
            quest = quests.get(quest_id)
            if quest is None:
                raise ValueError(f"未知任务：{quest_id}")
            if quest_id in self.completed:
                raise ValueError(f"任务已完成：{quest_id}")

            target = self.session.scene_spawn(quest["scene_id"])
            travel = travel_minutes(self.simulation.position_m, target)
            if travel:
                self.simulation.tick(travel, step=min(60, travel))
            self.simulation.move_to(target)

            minute = self.simulation.absolute_minute
            snapshot = self.simulation.snapshot()
            entry = build_quest_beat(
                self.session,
                quest,
                minute=minute,
                weather=self.simulation.weather,
                ambient_temperature_c=float(snapshot["ambient_temperature_c"]),
            )
            self.simulation.tick(int(quest["duration_min"]), step=60)
            result = self.session.apply_operations(
                quest_operations(self.session, quest, entry)
            )
            if not result.accepted:
                first = result.errors[0]["message"] if result.errors else "未知校验错误"
                raise ValueError(f"任务剧情未能落盘：{first}")
            self._invalidate_scenes()

            self.completed.add(quest_id)
            record = {
                "day": minute // 1440 + 1,
                "time": f"{(minute % 1440) // 60:02d}:{minute % 60:02d}",
                "quest_id": quest_id,
                "title": quest.get("title"),
                "scene_id": quest.get("scene_id"),
                "action": quest.get("action"),
                "travel_min": travel,
                "duration_min": int(quest["duration_min"]),
            }
            self.actions.append(record)
            return {
                "accepted": True,
                "message": f"完成「{quest.get('title')}」：{entry['summary']}",
                "story": entry,
                "action": record,
                "state": self._state(),
            }

    def sensory_plan(
        self,
        *,
        events: dict[str, Any] | list[dict[str, Any]] | None = None,
        preset: str | None = None,
        include_status: bool = False,
    ) -> dict[str, Any]:
        with self.lock:
            merged: list[dict[str, Any]] = []
            if isinstance(events, dict):
                merged.append(events)
            elif isinstance(events, list):
                merged.extend(events)

            if preset and preset != "status":
                template = SENSORY_PRESETS.get(preset)
                if template is None:
                    raise ValueError(f"未知感官预设：{preset}（可选 impact/step/voice/scent/taste/status）")
                kind = {"scent": "scent_release"}.get(preset, preset)
                merged.append(
                    {
                        "schema_version": "0.1.0",
                        "id": f"preset_{preset}",
                        "kind": kind,
                        "t_ms": 0,
                        "duration_ms": 0,
                        "intensity": template["intensity"],
                        "priority": template["priority"],
                        "focus": True,
                        "source": {"type": "player", "id": "player"},
                        "channels": template["channels"],
                    }
                )

            if include_status or preset == "status":
                effects = (self.session.modules.get("player_body") or {}).get("status_effects") or []
                merged.extend(status_effects_as_events(effects, t_ms=0))

            plan = self.bus.plan(merged)
            self.last_sensory = plan
            diagnostics = plan["diagnostics"]
            message = (
                f"感知事件：派发 {diagnostics['deliveries']} 条，丢弃 {diagnostics['dropped']} 条，"
                f"同步降级 {diagnostics['sync_violations']}"
            )
            return {"accepted": plan["ok"], "message": message, "state": self._state()}

    def interact(
        self,
        target: str,
        target_id: str,
        action: str,
        *,
        position_m: list[float] | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        """对 NPC 或场景物件做一次开放式交互。"""
        with self.lock:
            scenes = self._scene_entries()
            scene_index = {item["id"]: item for item in scenes}
            weather = self.simulation.weather or {}
            weather_kind = weather.get("kind")
            minute = self.simulation.absolute_minute
            nearby = position_m if isinstance(position_m, list) and len(position_m) == 3 else None

            if target == "npc":
                state = self.simulation.npcs.get(target_id)
                if state is None:
                    raise ValueError(f"未知 NPC：{target_id}")
                if action not in NPC_ACTIONS:
                    raise ValueError(f"NPC 不支持的动作：{action}")
                views = self._npc_views([state], scene_index)
                if not views:
                    raise ValueError("该 NPC 当前不在场景中")
                view = views[0]
                if nearby and math.dist(nearby, view["position_m"]) > 8.0:
                    raise ValueError("离得太远了，走近一点再试")
                npc = self._roster.get(target_id, {"id": target_id, "name": view["name"]})
                turn = self.talk_counts.get(target_id, 0) + 1
                self.talk_counts[target_id] = turn
                activity = state.get("activity")

                if action == "talk":
                    speech = talk_line(
                        npc,
                        activity=activity,
                        weather_kind=weather_kind,
                        minute=minute,
                        turn=turn,
                    )
                    delta, fx, message = 0.04, "talk", f"你和{view['name']}聊了几句。"
                elif action == "dance":
                    speech = dance_line(npc, activity=activity, turn=turn)
                    delta = 0.08 if activity != "sleep" else -0.02
                    fx, message = "dance", f"你邀请{view['name']}共舞。"
                elif action == "hit":
                    speech = hit_line(npc, activity=activity, turn=turn)
                    delta, fx, message = -0.25, "hit", f"你动手打了{view['name']}。"
                else:
                    speech = observe_line(npc)
                    delta, fx, message = 0.0, "observe", f"你观察了{view['name']}。"

                trust = min(1.0, max(0.0, self.trust.get(target_id, 0.5) + delta))
                self.trust[target_id] = trust
                self.simulation.events.append(
                    {
                        "minute": minute,
                        "type": "interaction",
                        "value": f"{view['name']} · {NPC_ACTIONS[action]}",
                    }
                )
                self.interactions.append(
                    {
                        "minute": minute,
                        "target": "npc",
                        "name": view["name"],
                        "action": NPC_ACTIONS[action],
                        "summary": speech,
                    }
                )
                self._interaction_sensory(action)
                self._save_state()
                return {
                    "accepted": True,
                    "message": message,
                    "speech": speech,
                    "fx": fx,
                    "trust": round(trust, 2),
                    "trust_label": npc_trust_label(trust),
                    "effects": {},
                    "state": self._state(),
                }

            if target == "prop":
                prop = next((item for item in self.props if item["id"] == target_id), None)
                if prop is None:
                    raise ValueError(f"未知物件：{target_id}")
                if action not in PROP_ACTIONS:
                    raise ValueError(f"物件不支持的动作：{action}")
                if nearby and math.dist(nearby, prop["position_m"]) > 7.0:
                    raise ValueError("离得太远了，走近一点再试")
                response = prop_response(
                    prop,
                    action,
                    picked=target_id in self.picked_props,
                    raining=weather_kind in ("rain", "storm"),
                    note_text=text if text else self.prop_notes.get(target_id),
                    carried=str((self.carried or {}).get("name") or "") or None,
                )
                applied = apply_effects(self.simulation, response["effects"])
                if response.get("picked"):
                    self.picked_props.add(target_id)
                if response.get("note"):
                    self.prop_notes[target_id] = response["note"]
                if response.get("carry"):
                    self.carried = {
                        "id": target_id,
                        "kind": prop["kind"],
                        "name": prop["name"],
                        "scene_id": prop["scene_id"],
                    }
                self.simulation.events.append(
                    {
                        "minute": minute,
                        "type": "interaction",
                        "value": f"{prop['name']} · {PROP_ACTIONS[action]}",
                    }
                )
                self.interactions.append(
                    {
                        "minute": minute,
                        "target": "prop",
                        "name": prop["name"],
                        "action": PROP_ACTIONS[action],
                        "summary": response["message"],
                    }
                )
                self._interaction_sensory("collect" if action == "harvest" else "use")
                self._save_state()
                return {
                    "accepted": True,
                    "message": response["message"],
                    "speech": None,
                    "fx": action,
                    "trust": None,
                    "trust_label": None,
                    "effects": applied,
                    "carried": self.carried,
                    "state": self._state(),
                }

            if target == "carry":
                if action != "drop":
                    raise ValueError(f"肩上物件不支持的动作：{action}")
                held = self.carried
                if held is None:
                    raise ValueError("你肩上没有东西")
                self.carried = None
                # 放下的物件落在玩家脚边，而不是跳回原位。
                prop = next((item for item in self.props if item["id"] == held["id"]), None)
                spot = self.simulation.position_m
                if prop is not None and spot:
                    scene = next(
                        (item for item in scenes if item["id"] == held.get("scene_id")), None
                    )
                    x, z = float(spot[0]), float(spot[2])
                    if scene is not None:
                        low = scene["bounds_m"]["min"]
                        high = scene["bounds_m"]["max"]
                        x = min(max(x, low[0] + 2), high[0] - 2)
                        z = min(max(z, low[2] + 2), high[2] - 2)
                    prop["position_m"] = [round(x, 2), prop["position_m"][1], round(z, 2)]
                message = f"你把{held['name']}放下，活动了一下发酸的肩。"
                self.simulation.events.append(
                    {"minute": minute, "type": "interaction", "value": f"{held['name']} · 放下"}
                )
                self.interactions.append(
                    {
                        "minute": minute,
                        "target": "carry",
                        "name": held["name"],
                        "action": "放下",
                        "summary": message,
                    }
                )
                self._interaction_sensory("use")
                self._save_state()
                return {
                    "accepted": True,
                    "message": message,
                    "speech": None,
                    "fx": "drop",
                    "trust": None,
                    "trust_label": None,
                    "effects": {},
                    "carried": None,
                    "state": self._state(),
                }

            raise ValueError(f"未知交互目标：{target}")

    def _interaction_sensory(self, action: str) -> None:
        preset = {
            "talk": ("voice", {"audio": {"cue": "vo_npc_talk", "layer": "near"}}, 0.55, 0.55),
            "dance": ("voice", {"audio": {"cue": "mus_dance", "layer": "near"}}, 0.6, 0.45),
            "hit": (
                "impact",
                {
                    "visual": {"vfx": "vfx_impact_dust", "screen_shake": 0.35},
                    "audio": {"cue": "sfx_stone_impact", "material": "stone", "layer": "near"},
                    "haptic": {"event": "haptic_impact_heavy", "region": "hands"},
                },
                0.85,
                0.9,
            ),
            "collect": ("step", {"audio": {"cue": "sfx_footstep_grass", "material": "grass", "layer": "near"}}, 0.4, 0.35),
            "use": ("ui", {"audio": {"cue": "ui_confirm", "layer": "near"}}, 0.35, 0.3),
        }.get(action)
        if preset is None:
            return
        kind, channels, intensity, priority = preset
        event = {
            "schema_version": "0.1.0",
            "id": f"interact_{action}",
            "kind": kind,
            "t_ms": 0,
            "duration_ms": 0,
            "intensity": intensity,
            "priority": priority,
            "focus": True,
            "source": {"type": "player", "id": "player"},
            "channels": channels,
        }
        self.last_sensory = self.bus.plan([event])

    def _state(self) -> dict[str, Any]:
        snapshot = self.simulation.snapshot()
        narrative = self.session.modules.get("narrative", {})
        manifest = self.session.manifest or {}
        modules = self.session.modules
        scenes = [
            {"id": item.get("id"), "name": item.get("name")}
            for item in modules.get("scenes", {}).get("scenes", [])
            if isinstance(item, dict) and item.get("id")
        ]
        physics = modules.get("physics", {}) or {}
        body = modules.get("player_body", {}) or {}
        ecology = modules.get("ecology", {}) or {}
        vital_cfg = body.get("vitals", {}) or {}
        climate = ecology.get("climate", {}) or {}
        return {
            "world": {
                "id": manifest.get("world_id"),
                "name": manifest.get("world_name"),
                "root": str(self.session.root),
                "position_m": self.simulation.position_m,
                "scene": snapshot.get("scene"),
                "scenes": scenes,
            },
            "model": {
                "template": manifest.get("template"),
                "modules": [
                    {"id": key, "schema_version": value.get("schema_version")}
                    for key, value in sorted(modules.items())
                    if isinstance(value, dict)
                ],
                "npc_count": len(modules.get("npc_agent", {}).get("npc_roster", [])),
                "nations": [
                    {"id": item.get("id"), "name": item.get("name")}
                    for item in narrative.get("nations", [])
                ],
                "classes": [
                    {"id": item.get("id"), "name": item.get("name")}
                    for item in narrative.get("classes", [])
                ],
            },
            "physics": {
                "gravity": physics.get("gravity"),
                "air_density": physics.get("air_density"),
                "speed_of_sound": physics.get("speed_of_sound"),
                "friction": physics.get("friction"),
                "thermal": physics.get("thermal"),
                "combustion": physics.get("combustion"),
                "deformation": physics.get("deformation"),
                "coupling_tick_order": physics.get("coupling_tick_order"),
            },
            "initial": {
                "template": manifest.get("template"),
                "created_at": manifest.get("created_at"),
                "seed": self.seed,
                "spawn": self.spawn,
                "vitals": {
                    "temperature_c": (vital_cfg.get("temperature") or {}).get("baseline_c"),
                    "hunger_drain_per_hour": (vital_cfg.get("hunger") or {}).get("drain_per_hour"),
                    "thirst_drain_per_hour": (vital_cfg.get("thirst") or {}).get("drain_per_hour"),
                    "survival": body.get("survival"),
                },
                "climate": {
                    "base_temperature_c": climate.get("base_temperature_c"),
                    "seasonal_amplitude_c": climate.get("seasonal_amplitude_c"),
                    "precipitation_scale": climate.get("precipitation_scale"),
                },
            },
            "quests": quest_list(self.session, self.completed),
            "actions": self.actions[-12:],
            "interactions": self.interactions[-8:],
            "carried": self.carried,
            "timeflow": {"recap": self.recap},
            "snapshot": snapshot,
            "narrative": {
                "tensions": narrative.get("tensions", []),
                "chronicle": narrative.get("chronicle", [])[-8:],
            },
            "events": self.simulation.events[-24:],
            "sensory": {"config": self.bus.describe(), "last": self.last_sensory},
        }

    def view(self) -> dict[str, Any]:
        """3D 客户端视图：只含渲染与 HUD 需要的字段，坐标统一为米。"""
        with self.lock:
            return self._view()

    def _raw_scenes(self) -> dict[str, dict[str, Any]]:
        return {
            item.get("id"): item
            for item in (self.session.modules.get("scenes", {}) or {}).get("scenes", [])
            if isinstance(item, dict) and item.get("id")
        }

    def _invalidate_scenes(self) -> None:
        self._scene_cache = None

    def _scene_entries(self) -> list[dict[str, Any]]:
        """场景条目在运行期保持不变（道具位置通过共享 dict 引用自动更新），
        因此只构建一次；reset/advance/act 后失效重建。"""
        if self._scene_cache is not None:
            return self._scene_cache
        raw_scenes = self._raw_scenes()
        props_by_scene: dict[str, list[dict[str, Any]]] = {}
        for prop in self.props:
            props_by_scene.setdefault(prop["scene_id"], []).append(prop)
        scenes: list[dict[str, Any]] = []
        for summary in self.session.query_scenes():
            raw = raw_scenes.get(summary["id"], {})
            ground, accent = _scene_palette(raw)
            low = summary["world_bounds_m"]["min"]
            high = summary["world_bounds_m"]["max"]
            scenes.append(
                {
                    "id": summary["id"],
                    "name": summary["name"],
                    "category": summary["category"],
                    "biome_ref": summary.get("biome_ref"),
                    "bounds_m": {"min": low, "max": high},
                    "center_m": [round((low[axis] + high[axis]) / 2, 3) for axis in range(3)],
                    "size_m": [round(high[axis] - low[axis], 3) for axis in range(3)],
                    "ground_color": ground,
                    "accent_color": accent,
                    "prop_seed": _stable_seed(summary["id"]) % 10000,
                    "gravity": summary.get("gravity"),
                    "medium": summary.get("medium"),
                    "ambient_temperature_c": summary.get("ambient_temperature_c"),
                    "props": props_by_scene.get(summary["id"], []),
                }
            )
        self._scene_cache = scenes
        return scenes

    def _view(self) -> dict[str, Any]:
        snapshot = self.simulation.snapshot()
        modules = self.session.modules
        manifest = self.session.manifest or {}
        narrative = modules.get("narrative", {}) or {}

        scenes = self._scene_entries()
        scene_index = {item["id"]: item for item in scenes}

        position = snapshot.get("position_m")
        current = snapshot.get("scene") or {}
        box = scene_index.get(current.get("id"))
        if position:
            floor_y = box["bounds_m"]["min"][1] if box else float(position[1])
            feet = [float(position[0]), floor_y, float(position[2])]
        else:
            feet = None

        climate = (modules.get("ecology", {}) or {}).get("climate", {}) or {}
        wind_cfg = climate.get("prevailing_wind", {}) or {}
        direction = math.radians(float(wind_cfg.get("direction_deg", 0)))
        speed = float(wind_cfg.get("speed_mps", 0))
        wind = [round(math.sin(direction) * speed, 3), 0.0, round(math.cos(direction) * speed, 3)]

        minute = int(snapshot["clock"]["minute"])
        solar = math.sin(math.pi * (minute - 360) / 720)
        # 天空亮度随太阳高度平滑变化，日出/日落附近仍保留晨昏色。
        daylight = min(1.0, max(0.0, (solar * 90 + 6) / 60))
        weather = snapshot.get("weather") or {}
        kind = weather.get("kind") or "clear"

        player = snapshot.get("player") or {}
        effects = snapshot.get("effects") or []
        return {
            "world": {
                "id": manifest.get("world_id"),
                "name": manifest.get("world_name"),
                "template": manifest.get("template"),
            },
            "time": {
                "day": snapshot["clock"]["day"],
                "clock": snapshot["clock"]["time"],
                "minute": minute,
                "daylight": round(daylight, 3),
                "sun": {
                    "azimuth_deg": round((minute / 1440) * 360, 2),
                    "elevation_deg": round(90 * solar, 2),
                },
            },
            "weather": {
                "kind": kind,
                "label": WEATHER_LABELS.get(kind, kind),
                "intensity": float(weather.get("intensity", 1.0)),
                "temperature_c": snapshot.get("ambient_temperature_c"),
                "wind": wind,
            },
            "camera": {"eye_height_m": 1.65, "third_person_distance_m": 5.4},
            "player": {
                "feet_m": feet,
                "position_m": position,
                "scene_id": current.get("id"),
                "vitals": player,
                "survival": snapshot.get("survival") or {},
                "effects": effects,
            },
            "npcs": self._npc_views(snapshot.get("npcs") or [], scene_index),
            "scenes": scenes,
            "props": self.props,
            "carried": self.carried,
            "interactions": self.interactions[-8:],
            "quests": quest_list(self.session, self.completed),
            "tensions": narrative.get("tensions", []),
            "chronicle": narrative.get("chronicle", [])[-12:],
            "events": self.simulation.events[-40:],
            "recap": self.recap,
            "actions": self.actions[-12:],
        }

    def _npc_views(self, npcs: list[dict[str, Any]], scene_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """把日程状态投影到场景内的稳定坐标；同一 NPC 同一地点不会瞬移。"""
        fallback = next(iter(scene_index.values()), None)
        result: list[dict[str, Any]] = []
        for index, npc in enumerate(npcs):
            scene = scene_index.get(str(npc.get("scene_id"))) or fallback
            if scene is None:
                continue
            low = scene["bounds_m"]["min"]
            high = scene["bounds_m"]["max"]
            width = max(high[0] - low[0], 2)
            depth = max(high[2] - low[2], 2)
            seed = _stable_seed(npc.get("npc_id"), npc.get("location"), npc.get("activity"))
            angle = math.radians(seed % 360) + index * 0.9
            radius = min(width, depth) * (0.16 + (seed // 360 % 100) / 100 * 0.28)
            x = min(max(low[0] + width / 2 + math.cos(angle) * radius, low[0] + 2), high[0] - 2)
            z = min(max(low[2] + depth / 2 + math.sin(angle) * radius, low[2] + 2), high[2] - 2)
            trust = self.trust.get(str(npc.get("npc_id")), 0.5)
            result.append(
                {
                    "id": npc.get("npc_id"),
                    "name": npc.get("name") or npc.get("npc_id"),
                    "layer": npc.get("layer"),
                    "scene_id": scene["id"],
                    "activity": npc.get("activity"),
                    "location": npc.get("location"),
                    "status": npc.get("status"),
                    "position_m": [round(x, 3), low[1], round(z, 3)],
                    "color": NPC_LAYER_COLORS.get(str(npc.get("layer")), "#7cc7e8"),
                    "trust": round(trust, 2),
                    "trust_label": npc_trust_label(trust),
                }
            )
        return result

    def state(self) -> dict[str, Any]:
        with self.lock:
            return self._state()


# 兼容旧导入路径：HTTP 层已拆到 dreamweave.server。
from .server import create_server  # noqa: E402  isort: skip
