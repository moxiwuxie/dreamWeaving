"""M006-01/02 世界查询与修改 API。

所有修改走同一条路径：内存态补丁 -> 全量校验 -> 通过才落盘，失败自动回滚。
对应 global_constraints.edit_principle「编辑即时生效、可回滚」。
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import MODULE_SCHEMAS
from .validator import Report, _find_manifest, validate_world


class WorldError(Exception):
    """世界包无法加载或路径非法。"""


def _stage_json(path: Path, data: Any) -> Path:
    """把 JSON 写入同目录临时文件，返回临时路径；调用方负责 os.replace 或清理。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return Path(handle.name)


@dataclass
class ApplyResult:
    accepted: bool
    operations: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "message": self.message,
            "operations": self.operations,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class WorldSession:
    """一个可编辑世界包的运行时句柄。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        manifest_path = _find_manifest(self.root)
        if manifest_path is None:
            raise WorldError(f"未找到 world.json：{self.root}")
        self.manifest_path = manifest_path
        self.manifest: dict[str, Any] = {}
        self.modules: dict[str, Any] = {}
        self._history: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.reload()

    # ---------- 加载与落盘 ----------

    def reload(self) -> None:
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.modules = {}
        for key in MODULE_SCHEMAS:
            path = self._module_path(key)
            if path is not None and path.is_file():
                self.modules[key] = json.loads(path.read_text(encoding="utf-8"))

    def _module_path(self, key: str) -> Path | None:
        rel = self.manifest.get("modules", {}).get(key, {}).get("path")
        if not isinstance(rel, str):
            return None
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            raise WorldError(f"模块路径越界：{rel}")
        return path

    def _commit_modules(self, data: dict[str, Any], keys: list[str]) -> None:
        """先全部写临时文件，再逐个替换，尽量避免多文件写一半的中间态。"""
        staged: list[tuple[Path, Path]] = []
        try:
            for key in keys:
                path = self._module_path(key)
                if path is None:
                    raise WorldError(f"清单缺少模块路径：{key}")
                staged.append((path, _stage_json(path, data[key])))
            for path, temp in staged:
                os.replace(temp, path)
        except BaseException:
            for _, temp in staged:
                temp.unlink(missing_ok=True)
            raise

    def save(self) -> None:
        self._commit_modules(self.modules, list(self.modules))

    # ---------- M006-01 查询 ----------

    def get_world_physics(self) -> dict[str, Any]:
        return copy.deepcopy(self.modules.get("physics", {}))

    def query_npc_list(self, layer: str | None = None, scene_id: str | None = None) -> list[dict[str, Any]]:
        roster = self.modules.get("npc_agent", {}).get("npc_roster", [])
        return [
            {
                "id": npc.get("id"),
                "name": npc.get("name"),
                "layer": npc.get("layer"),
                "scene_id": npc.get("scene_id"),
                "schedule_template": npc.get("schedule_template"),
                "goals": copy.deepcopy(npc.get("persona", {}).get("goals", [])),
            }
            for npc in roster
            if (layer is None or npc.get("layer") == layer)
            and (scene_id is None or npc.get("scene_id") == scene_id)
        ]

    def query_biome_stats(self) -> list[dict[str, Any]]:
        ecology = self.modules.get("ecology", {})
        species = ecology.get("species", [])
        stats = []
        for biome in ecology.get("ecosystem", {}).get("biomes", []):
            residents = [item for item in species if biome.get("id") in item.get("habitats", [])]
            stats.append(
                {
                    "biome": biome.get("id"),
                    "species_count": len(residents),
                    "species": [item.get("id") for item in residents],
                    "total_biomass_kg": round(sum(item.get("average_mass_kg", 0) for item in residents), 2),
                }
            )
        return stats

    def get_weather(self) -> dict[str, Any]:
        climate = self.modules.get("ecology", {}).get("climate", {})
        return {
            "base_temperature_c": climate.get("base_temperature_c"),
            "seasonal_amplitude_c": climate.get("seasonal_amplitude_c"),
            "precipitation_scale": climate.get("precipitation_scale"),
            "prevailing_wind": copy.deepcopy(climate.get("prevailing_wind", {})),
            "events": [
                {"id": event.get("id"), "kind": event.get("kind"), "probability_per_day": event.get("probability_per_day")}
                for event in climate.get("weather_events", [])
            ],
        }

    def query_scenes(self) -> list[dict[str, Any]]:
        """列出包内场景，并把 Chunk 包围盒换算为米制，便于引擎与调试直接使用。"""
        scenes = self.modules.get("scenes", {}).get("scenes", [])
        scale = self._chunk_scale_m()
        result: list[dict[str, Any]] = []
        for scene in scenes:
            summary = self._scene_summary(scene, scale)
            if summary is not None:
                result.append(summary)
        return result

    def locate_scene(self, position_m: list[float] | tuple[float, float, float]) -> dict[str, Any]:
        """把世界坐标（米）换算成 Chunk 坐标并命中场景；未命中时 scene 为 None。"""
        try:
            coords = [float(value) for value in position_m]
        except (TypeError, ValueError) as exc:
            raise WorldError(f"坐标必须由三个数字组成：{position_m!r}") from exc
        if len(coords) != 3:
            raise WorldError(f"坐标必须为 [x, y, z]：{position_m!r}")

        scale = self._chunk_scale_m()
        chunk = [math.floor(value / scale[axis]) for axis, value in enumerate(coords)]
        bounds = self.manifest.get("terrain", {}).get("world_bounds_chunks") or []
        inside_world = len(bounds) == 3 and all(0 <= chunk[axis] < bounds[axis] for axis in range(3))

        payload: dict[str, Any] = {
            "position_m": coords,
            "chunk": chunk,
            "inside_world": inside_world,
            "scene": None,
        }
        for scene in self.modules.get("scenes", {}).get("scenes", []):
            low = self._chunk_corner(scene, "min_chunk")
            high = self._chunk_corner(scene, "max_chunk")
            if low is None or high is None:
                continue
            if all(low[axis] <= chunk[axis] <= high[axis] for axis in range(3)):
                payload["scene"] = {
                    "id": scene.get("id"),
                    "name": scene.get("name"),
                    "category": scene.get("category"),
                    "bounds": {"min_chunk": low, "max_chunk": high},
                    "uef": copy.deepcopy(scene.get("uef", {})),
                    "hazards": copy.deepcopy(scene.get("hazards", [])),
                }
                break
        return payload

    def scene_spawn(self, scene_id: str | None = None) -> list[float]:
        """返回场景几何中心的世界坐标（米），缺省取第一个场景。"""
        scenes = [
            item
            for item in self.modules.get("scenes", {}).get("scenes", [])
            if isinstance(item, dict) and item.get("id")
        ]
        if not scenes:
            raise WorldError("世界包没有可用场景")
        scene = scenes[0]
        if scene_id is not None:
            scene = next((item for item in scenes if item.get("id") == scene_id), None)
            if scene is None:
                raise WorldError(f"未知场景：{scene_id}")
        low = self._chunk_corner(scene, "min_chunk")
        high = self._chunk_corner(scene, "max_chunk")
        if low is None or high is None:
            raise WorldError(f"场景缺少边界：{scene.get('id')}")
        scale = self._chunk_scale_m()
        return [round(((low[axis] + high[axis]) / 2 + 0.5) * scale[axis], 3) for axis in range(3)]

    # ---------- 场景层内部工具 ----------

    def _chunk_scale_m(self) -> tuple[float, float, float]:
        terrain = self.manifest.get("terrain", {})
        voxel = float(terrain.get("voxel_size_m") or 0.1)
        dims = terrain.get("chunk_dim") or [32, 32, 32]
        return (voxel * float(dims[0]), voxel * float(dims[1]), voxel * float(dims[2]))

    @staticmethod
    def _chunk_corner(scene: dict[str, Any], key: str) -> list[int] | None:
        value = (scene.get("bounds") or {}).get(key)
        if isinstance(value, list) and len(value) == 3 and all(isinstance(item, int) for item in value):
            return list(value)
        return None

    def _scene_summary(self, scene: dict[str, Any], scale: tuple[float, float, float]) -> dict[str, Any] | None:
        low = self._chunk_corner(scene, "min_chunk")
        high = self._chunk_corner(scene, "max_chunk")
        if low is None or high is None:
            return None
        uef = scene.get("uef") or {}
        return {
            "id": scene.get("id"),
            "name": scene.get("name"),
            "category": scene.get("category"),
            "biome_ref": scene.get("biome_ref"),
            "bounds": {"min_chunk": low, "max_chunk": high},
            "world_bounds_m": {
                "min": [round(low[axis] * scale[axis], 3) for axis in range(3)],
                "max": [round((high[axis] + 1) * scale[axis], 3) for axis in range(3)],
            },
            "volume_chunks": (high[0] - low[0] + 1) * (high[1] - low[1] + 1) * (high[2] - low[2] + 1),
            "gravity": uef.get("gravity"),
            "medium": (uef.get("medium") or {}).get("kind"),
            "ambient_temperature_c": (uef.get("energy") or {}).get("ambient_temperature_c"),
            "hazard_count": len(scene.get("hazards") or []),
        }

    # ---------- M006-02 修改 ----------

    def set_physics_param(self, param_path: str, value: Any, dry_run: bool = False) -> ApplyResult:
        return self.apply_operations([{"op": "set", "path": f"physics.{param_path}", "value": value}], dry_run=dry_run)

    def spawn_npc(self, npc: dict[str, Any], dry_run: bool = False) -> ApplyResult:
        return self.apply_operations([{"op": "append", "path": "npc_agent.npc_roster", "value": npc}], dry_run=dry_run)

    def trigger_weather_event(self, event_id: str) -> dict[str, Any]:
        """运行时触发天气，不写配置；由引擎在事件结束后回写统计。"""
        events = self.modules.get("ecology", {}).get("climate", {}).get("weather_events", [])
        for event in events:
            if event.get("id") == event_id:
                return {
                    "triggered": True,
                    "event": copy.deepcopy(event),
                    "duration_min": event.get("duration_min"),
                    "intensity": event.get("intensity"),
                }
        return {"triggered": False, "event_id": event_id, "reason": "未找到该天气事件"}

    def apply_operations(self, operations: list[dict[str, Any]], dry_run: bool = False) -> ApplyResult:
        if not operations:
            return ApplyResult(False, message="补丁为空")

        candidate = copy.deepcopy(self.modules)
        changed: set[str] = set()
        for index, operation in enumerate(operations):
            try:
                module_key = self._apply_one(candidate, operation)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                return ApplyResult(False, operations, f"第 {index + 1} 条操作无法执行：{exc}", errors=[{"code": "PATCH_INVALID", "path": operation.get("path", ""), "message": str(exc)}])
            changed.add(module_key)

        report: Report = validate_world(self.root, modules_override=candidate)
        if not report.ok:
            return ApplyResult(
                False,
                operations,
                "补丁被拒绝：修改后世界规则自洽性校验未通过",
                errors=[issue.to_dict() for issue in report.errors],
                warnings=[issue.to_dict() for issue in report.warnings],
            )

        result = ApplyResult(
            True,
            operations,
            "修改已生效" if not dry_run else "修改可应用（dry-run，未落盘）",
            warnings=[issue.to_dict() for issue in report.warnings],
        )
        if dry_run:
            return result

        try:
            self._commit_modules(candidate, sorted(changed))
        except (OSError, WorldError) as exc:
            return ApplyResult(False, operations, f"写入模块失败：{exc}")
        self._history.append((copy.deepcopy(self.modules), copy.deepcopy(self.manifest)))
        self.modules = candidate
        return result

    def _apply_one(self, modules: dict[str, Any], operation: dict[str, Any]) -> str:
        op = operation.get("op", "set")
        path = operation.get("path", "")
        if not isinstance(path, str) or "." not in path:
            raise ValueError(f"路径必须为 模块.字段 形式：{path!r}")
        module_key, _, rest = path.partition(".")
        if module_key not in MODULE_SCHEMAS:
            raise ValueError(f"未知模块：{module_key}")
        if module_key not in modules:
            raise ValueError(f"世界包未包含模块：{module_key}")

        parts = rest.split(".")
        root = modules[module_key]
        last = parts[-1]

        if op == "append":
            container = self._walk(root, parts)
            if not isinstance(container, list):
                raise TypeError(f"append 目标必须是数组：{rest}")
            container.append(operation.get("value"))
            return module_key

        target = self._walk(root, parts[:-1])
        if op == "set":
            if isinstance(target, list):
                target[int(last)] = operation.get("value")
            else:
                if last not in target:
                    raise KeyError(f"字段不存在，拒绝新增未知字段：{rest}")
                target[last] = operation.get("value")
        elif op == "remove":
            if isinstance(target, list):
                target.pop(int(last))
            else:
                del target[last]
        else:
            raise ValueError(f"不支持的操作：{op}")
        return module_key

    @staticmethod
    def _walk(node: Any, parts: list[str]) -> Any:
        for part in parts:
            node = node[int(part)] if isinstance(node, list) else node[part]
        return node

    def undo(self) -> bool:
        if not self._history:
            return False
        self.modules, self.manifest = self._history.pop()
        self.save()
        return True
