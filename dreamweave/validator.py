"""世界配置校验：结构校验（JSON Schema）+ 跨文件自洽性检查 + 算力/体验风险提示。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import MANIFEST_SCHEMA, MODULE_SCHEMAS, SchemaSet

EARTH_GRAVITY = -9.81
ALL_TICK_STAGES = ("thermal", "fluid", "combustion", "deformation", "rigid_body")


@dataclass
class Issue:
    level: str
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "path": self.path, "message": self.message}


@dataclass
class Report:
    target: str
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    loaded_modules: list[str] = field(default_factory=list)

    def error(self, code: str, path: str, message: str) -> None:
        self.errors.append(Issue("error", code, path, message))

    def warn(self, code: str, path: str, message: str) -> None:
        self.warnings.append(Issue("warning", code, path, message))

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target": self.target,
            "loaded_modules": self.loaded_modules,
            "summary": {"errors": len(self.errors), "warnings": len(self.warnings)},
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def _get(data: Any, *keys: str, default: Any = None) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _duplicate_ids(items: Any, key: str = "id") -> list[str]:
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        value = item.get(key) if isinstance(item, dict) else None
        if not isinstance(value, str):
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def _find_manifest(target: Path) -> Path | None:
    if target.is_file():
        return target
    for name in ("world.json", "manifest.json"):
        candidate = target / name
        if candidate.is_file():
            return candidate
    found = sorted(target.glob("*.world.json"))
    return found[0] if found else None


def validate_world(
    target: str | Path,
    schema_set: SchemaSet | None = None,
    modules_override: dict[str, Any] | None = None,
) -> Report:
    """校验世界包。modules_override 用于编辑器在落盘前做内存态校验。"""
    target_path = Path(target).expanduser().resolve()
    report = Report(target=str(target_path))
    schemas = schema_set or SchemaSet()

    manifest_path = _find_manifest(target_path)
    if manifest_path is None:
        report.error("MANIFEST_NOT_FOUND", "/", f"未找到 world.json 或 *manifest*.json：{target_path}")
        return report
    root = manifest_path.parent

    try:
        manifest = _load_json(manifest_path)
    except json.JSONDecodeError as exc:
        report.error("JSON_INVALID", "world.json", f"清单 JSON 解析失败：{exc}")
        return report

    for pointer, message in schemas.iter_errors(manifest, MANIFEST_SCHEMA):
        report.error("MANIFEST_SCHEMA", f"world.json:{pointer}", message)

    _check_manifest_files(manifest, root, report)
    modules = _load_modules(manifest, root, schemas, report, modules_override)

    if (physics := modules.get("physics")) is not None:
        _check_physics(physics, report)
    if (ecology := modules.get("ecology")) is not None:
        _check_ecology(ecology, report)
    if (npc := modules.get("npc_agent")) is not None:
        _check_npc(npc, report)
    if (player := modules.get("player_body")) is not None:
        _check_player(player, report)

    _check_sensory(modules, report)
    if (bus := modules.get("sensory_bus")) is not None:
        _check_sensory_bus(bus, modules, report)
    if (scenes := modules.get("scenes")) is not None:
        _check_scenes(scenes, modules, manifest, report)
    if (narrative := modules.get("narrative")) is not None:
        _check_narrative(narrative, modules, report)
    _check_cross_module(modules, report)
    return report


def _check_manifest_files(manifest: dict[str, Any], root: Path, report: Report) -> None:
    for key, ref in (_get(manifest, "modules", default={}) or {}).items():
        rel = _get(ref, "path")
        if not isinstance(rel, str):
            continue
        path = (root / rel).resolve()
        if root not in path.parents:
            report.error("MODULE_PATH_ESCAPE", f"modules/{key}/path", f"模块路径越界：{rel}")
            continue
        if not path.is_file():
            report.error("MODULE_MISSING", f"modules/{key}/path", f"模块文件不存在：{rel}")
        elif (expected := _get(ref, "sha256")) and _sha256(path) != expected:
            report.error("MODULE_CHECKSUM", f"modules/{key}/sha256", f"{rel} 摘要与清单不一致")

    for asset in _get(manifest, "assets", default=[]) or []:
        rel = _get(asset, "path", default="")
        if not (root / rel).is_file():
            report.warn("ASSET_MISSING", f"assets/{rel}", f"资源未随包分发：{rel}")

    chunk_dim = _get(manifest, "terrain", "chunk_dim", default=[])
    if chunk_dim and chunk_dim != [32, 32, 32]:
        report.warn(
            "CHUNK_DIM_DEVIATION",
            "terrain/chunk_dim",
            f"Chunk 尺寸 {chunk_dim} 偏离 M003-01 约定的 32×32×32，将影响序列化兼容性",
        )


def _load_modules(
    manifest: dict[str, Any],
    root: Path,
    schemas: SchemaSet,
    report: Report,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    for key, schema_file in MODULE_SCHEMAS.items():
        rel = _get(manifest, "modules", key, "path")
        if not isinstance(rel, str):
            continue
        path = (root / rel).resolve()
        if override is not None and key in override:
            data = override[key]
        elif root not in path.parents or not path.is_file():
            continue
        else:
            try:
                data = _load_json(path)
            except json.JSONDecodeError as exc:
                report.error("JSON_INVALID", rel, f"JSON 解析失败：{exc}")
                continue
        has_error = False
        for pointer, message in schemas.iter_errors(data, schema_file):
            report.error("SCHEMA", f"{key}:{pointer}", message)
            has_error = True
        if not has_error:
            loaded[key] = data
            report.loaded_modules.append(key)
    return loaded


def _check_physics(physics: dict[str, Any], report: Report) -> None:
    gravity = physics.get("gravity")
    if gravity == 0:
        report.warn("PHYSICS_ZERO_G", "physics:/gravity", "重力为 0，流体与燃烧将失去自然对流，建议确认是否为刻意设定")
    elif isinstance(gravity, (int, float)) and abs(gravity - EARTH_GRAVITY) > 20:
        report.warn("PHYSICS_GRAVITY_EXTREME", "physics:/gravity", f"重力 {gravity} m/s² 远离地球基准，角色移动与坠落伤害需重调")

    friction = physics.get("friction") or {}
    static, dynamic = friction.get("static"), friction.get("dynamic")
    if isinstance(static, (int, float)) and isinstance(dynamic, (int, float)) and static < dynamic:
        report.warn("PHYSICS_FRICTION_ORDER", "physics:/friction", "静摩擦系数低于动摩擦，物体将无法自然静止")

    air_density = physics.get("air_density")
    speed_of_sound = physics.get("speed_of_sound")
    if isinstance(air_density, (int, float)) and isinstance(speed_of_sound, (int, float)):
        if air_density < 0.1 and speed_of_sound > 250:
            report.warn("PHYSICS_SOUND_MEDIUM", "physics:/speed_of_sound", "近真空环境中声速不应接近地球空气值")

    viscosity = _get(physics, "fluid", "viscosity")
    if isinstance(viscosity, (int, float)) and viscosity > 10:
        report.warn("PHYSICS_HIGH_VISCOSITY", "physics:/fluid/viscosity", "高粘度将显著抬高流体求解开销")

    substeps = _get(physics, "rigid_body", "substeps")
    if isinstance(substeps, int) and substeps > 8:
        report.warn("PHYSICS_SUBSTEPS_COST", "physics:/rigid_body/substeps", f"子步数 {substeps} 会成倍消耗物理算力")

    stages = physics.get("coupling_tick_order") or []
    missing = [stage for stage in ALL_TICK_STAGES if stage not in stages]
    if missing:
        report.warn("PHYSICS_TICK_INCOMPLETE", "physics:/coupling_tick_order", f"未声明的求解阶段将使用默认顺序：{', '.join(missing)}")

    ambient = _get(physics, "thermal", "ambient_temperature_c")
    if isinstance(ambient, (int, float)) and not -60 <= ambient <= 60:
        report.warn("PHYSICS_AMBIENT_EXTREME", "physics:/thermal/ambient_temperature_c", f"环境温度 {ambient}℃ 属极端工况，需确认玩家生理与植被配置同步")


def _check_ecology(ecology: dict[str, Any], report: Report) -> None:
    biomes = _get(ecology, "ecosystem", "biomes", default=[]) or []
    for dup in _duplicate_ids(biomes):
        report.error("ECOLOGY_DUPLICATE_BIOME", "ecology:/ecosystem/biomes", f"群落 id 重复：{dup}")
    biome_ids = {biome.get("id") for biome in biomes if isinstance(biome, dict)}

    for biome in biomes:
        if not isinstance(biome, dict):
            continue
        for field_name in ("temperature_range_c", "humidity_range"):
            bounds = biome.get(field_name)
            if isinstance(bounds, list) and len(bounds) == 2 and bounds[0] >= bounds[1]:
                report.error(
                    "ECOLOGY_RANGE_INVALID",
                    f"ecology:/ecosystem/biomes/{biome.get('id')}/{field_name}",
                    f"{field_name} 下界必须小于上界：{bounds}",
                )

    species = ecology.get("species") or []
    species_ids = {item.get("id") for item in species if isinstance(item, dict)}
    for dup in _duplicate_ids(species):
        report.error("ECOLOGY_DUPLICATE_SPECIES", "ecology:/species", f"物种 id 重复：{dup}")

    for item in species:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        base = f"ecology:/species/{sid}"
        for habitat in item.get("habitats") or []:
            if habitat not in biome_ids:
                report.error("ECOLOGY_UNKNOWN_BIOME", f"{base}/habitats", f"引用了不存在的群落：{habitat}")

        food_web = item.get("food_web") or {}
        for role in ("prey", "predators"):
            for ref in food_web.get(role) or []:
                if ref not in species_ids:
                    report.error("ECOLOGY_UNKNOWN_SPECIES", f"{base}/food_web/{role}", f"食物网引用了不存在的物种：{ref}")
                if ref == sid:
                    report.error("ECOLOGY_SELF_REFERENCE", f"{base}/food_web/{role}", "物种不能以自身为食或被自身捕食")

        if item.get("kingdom") in {"carnivore", "omnivore"} and not food_web.get("prey"):
            report.warn("ECOLOGY_NO_PREY", f"{base}/food_web/prey", "肉食/杂食物种未配置猎物，食物链将断裂")

        lifespan, maturity = item.get("lifespan_days"), item.get("maturity_days")
        if isinstance(lifespan, (int, float)) and isinstance(maturity, (int, float)) and maturity > lifespan:
            report.error("ECOLOGY_MATURITY_OVER_LIFESPAN", f"{base}/maturity_days", "成熟期超过寿命，物种无法繁殖")

    events = _get(ecology, "climate", "weather_events", default=[]) or []
    for dup in _duplicate_ids(events):
        report.error("ECOLOGY_DUPLICATE_EVENT", "ecology:/climate/weather_events", f"天气事件 id 重复：{dup}")
    if not events:
        report.warn("ECOLOGY_NO_WEATHER", "ecology:/climate/weather_events", "未配置天气事件，气象系统只输出基础环流")

    if _get(ecology, "ecosystem", "carrying_capacity_scale") == 0:
        report.warn("ECOLOGY_ZERO_CAPACITY", "ecology:/ecosystem/carrying_capacity_scale", "环境容量为 0，物种将只减不增")


def _check_npc(npc: dict[str, Any], report: Report) -> None:
    goods = _get(npc, "economy", "goods", default=[]) or []
    for dup in _duplicate_ids(goods):
        report.error("NPC_DUPLICATE_GOOD", "npc_agent:/economy/goods", f"商品 id 重复：{dup}")
    if not goods:
        report.warn("NPC_NO_GOODS", "npc_agent:/economy/goods", "未配置商品，经济内核无法形成供需")

    laws = npc.get("laws") or []
    for dup in _duplicate_ids(laws):
        report.error("NPC_DUPLICATE_LAW", "npc_agent:/laws", f"法律 id 重复：{dup}")
    if not laws:
        report.warn("NPC_NO_LAW", "npc_agent:/laws", "未定义法律，社会约束完全依赖 LLM 即兴判断")

    templates = npc.get("schedule_templates") or []
    template_ids = {item.get("id") for item in templates if isinstance(item, dict)}
    for dup in _duplicate_ids(templates):
        report.error("NPC_DUPLICATE_SCHEDULE", "npc_agent:/schedule_templates", f"日程模板 id 重复：{dup}")
    for template in templates:
        entries = template.get("entries") or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            start, end = entry.get("start_minute"), entry.get("end_minute")
            if isinstance(start, int) and isinstance(end, int) and start >= end:
                report.error(
                    "NPC_SCHEDULE_ORDER",
                    f"npc_agent:/schedule_templates/{template.get('id')}/entries",
                    f"日程段起始 {start} 必须小于结束 {end}",
                )
        ordered = sorted(
            (entry for entry in entries if isinstance(entry, dict) and "start_minute" in entry),
            key=lambda entry: entry["start_minute"],
        )
        for prev, cur in zip(ordered, ordered[1:]):
            if prev.get("end_minute", 0) > cur.get("start_minute", 0):
                report.warn(
                    "NPC_SCHEDULE_OVERLAP",
                    f"npc_agent:/schedule_templates/{template.get('id')}/entries",
                    f"日程段重叠：{prev.get('start_minute')}~{prev.get('end_minute')} 与 {cur.get('start_minute')}~{cur.get('end_minute')}",
                )

    roster = npc.get("npc_roster") or []
    roster_ids = {item.get("id") for item in roster if isinstance(item, dict)}
    for dup in _duplicate_ids(roster):
        report.error("NPC_DUPLICATE_ROSTER", "npc_agent:/npc_roster", f"NPC id 重复：{dup}")

    layers = npc.get("layers") or {}
    for agent in roster:
        if not isinstance(agent, dict):
            continue
        layer = agent.get("layer")
        if layer in layers and not _get(layers, layer, "enabled", default=True):
            report.error("NPC_LAYER_DISABLED", f"npc_agent:/npc_roster/{agent.get('id')}/layer", f"所属层 {layer} 已关闭")
        template = agent.get("schedule_template")
        if template and template not in template_ids:
            report.error("NPC_UNKNOWN_SCHEDULE", f"npc_agent:/npc_roster/{agent.get('id')}/schedule_template", f"引用了不存在的日程模板：{template}")
        for relation in agent.get("relations") or []:
            target = _get(relation, "target")
            if target and target not in roster_ids:
                report.error("NPC_UNKNOWN_RELATION", f"npc_agent:/npc_roster/{agent.get('id')}/relations", f"人际关系指向不存在的 NPC：{target}")

    llm = layers.get("llm_core") or {}
    if llm.get("enabled") and not _get(layers, "mass_life", "enabled", default=True):
        report.warn("NPC_LLM_WITHOUT_LIFE", "npc_agent:/layers", "LLM 核心 NPC 依赖日常居民层提供世界状态，建议同时开启 mass_life")
    if llm.get("enabled") and not llm.get("max_active_agents"):
        report.warn("NPC_LLM_INACTIVE", "npc_agent:/layers/llm_core/max_active_agents", "LLM 层已开启但并发数为 0，核心 NPC 不会激活")
    if llm.get("enabled") and not any(_get(agent, "layer") == "llm_core" for agent in roster if isinstance(agent, dict)):
        report.warn("NPC_LLM_NO_ROSTER", "npc_agent:/npc_roster", "LLM 层已开启但没有归属该层的 NPC")


def _check_player(player: dict[str, Any], report: Report) -> None:
    temperature = _get(player, "vitals", "temperature", default={}) or {}
    comfort = temperature.get("comfort_ambient_range_c") or []
    low, high = temperature.get("critical_low_c"), temperature.get("critical_high_c")
    baseline = temperature.get("baseline_c")
    if len(comfort) == 2 and comfort[0] >= comfort[1]:
        report.error(
            "PLAYER_COMFORT_INVALID",
            "player_body:/vitals/temperature/comfort_ambient_range_c",
            f"舒适环境温度下界必须小于上界：{comfort}",
        )
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        if low >= high:
            report.error("PLAYER_CRITICAL_INVALID", "player_body:/vitals/temperature", "核心体温临界下限必须小于上限")
        if isinstance(baseline, (int, float)) and not low < baseline < high:
            report.error("PLAYER_BASELINE_INVALID", "player_body:/vitals/temperature/baseline_c", "基础体温必须落在核心体温临界区间内")

    for vital in ("hunger", "thirst"):
        damage = _get(player, "vitals", vital, "damage_per_hour")
        if damage == 0:
            report.warn("PLAYER_NO_VITAL_DAMAGE", f"player_body:/vitals/{vital}/damage_per_hour", f"{vital} 耗尽后无伤害，生理压力将不产生后果")

    pain_curve = _get(player, "injury", "pain_curve", default=[]) or []
    inputs = [point.get("input") for point in pain_curve if isinstance(point, dict)]
    if any(isinstance(value, (int, float)) for value in inputs):
        numeric = [value for value in inputs if isinstance(value, (int, float))]
        if numeric != sorted(numeric):
            report.error("PLAYER_CURVE_NOT_MONOTONIC", "player_body:/injury/pain_curve", "疼痛曲线 input 必须单调递增")

    effects = player.get("status_effects") or []
    for dup in _duplicate_ids(effects):
        report.error("PLAYER_DUPLICATE_EFFECT", "player_body:/status_effects", f"状态效果 id 重复：{dup}")
    triggers = [effect.get("trigger") for effect in effects if isinstance(effect, dict)]
    duplicated_triggers = sorted({trigger for trigger in triggers if isinstance(trigger, str) and triggers.count(trigger) > 1})
    if duplicated_triggers:
        report.warn("PLAYER_TRIGGER_CONFLICT", "player_body:/status_effects", f"同一触发条件绑定了多个效果：{', '.join(duplicated_triggers)}")

    feedback_fields = ("vfx", "sfx", "haptic", "scent", "taste")
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        feedback = effect.get("feedback") or {}
        if not any(field_name in feedback for field_name in feedback_fields):
            report.warn("PLAYER_NO_FEEDBACK", f"player_body:/status_effects/{effect.get('id')}", "状态效果未绑定任何五感反馈，玩家将无感知")


def _check_sensory(modules: dict[str, Any], report: Report) -> None:
    """五感模块内部引用与降级链路检查。"""
    haptic = modules.get("haptic")
    if haptic:
        waves = haptic.get("vibration_wave_library") or []
        wave_ids = {wave.get("id") for wave in waves if isinstance(wave, dict)}
        for dup in _duplicate_ids(waves):
            report.error("HAPTIC_DUPLICATE_WAVE", "haptic:/vibration_wave_library", f"波形 id 重复：{dup}")

        seen_events: set[str] = set()
        for binding in haptic.get("event_wave_map") or []:
            wave_id = _get(binding, "wave_id")
            event = _get(binding, "event")
            if wave_id not in wave_ids:
                report.error("HAPTIC_UNKNOWN_WAVE", "haptic:/event_wave_map", f"事件 {event} 引用了不存在的波形：{wave_id}")
            if event in seen_events:
                report.warn("HAPTIC_DUPLICATE_EVENT", "haptic:/event_wave_map", f"事件 {event} 绑定了多条波形，将以最后一条为准")
            if isinstance(event, str):
                seen_events.add(event)

        devices = haptic.get("devices") or {}
        if not _get(devices, "dualsense", "enabled", default=False) and not _get(devices, "screen_rumble", "enabled", default=False):
            report.warn("HAPTIC_NO_OUTPUT", "haptic:/devices", "手柄与屏幕震动均关闭，触觉反馈将完全缺失")

    scent = modules.get("scent")
    if scent:
        lib = scent.get("scent_lib") or []
        scent_ids = {item.get("id") for item in lib if isinstance(item, dict)}
        for dup in _duplicate_ids(lib):
            report.error("SCENT_DUPLICATE", "scent:/scent_lib", f"气味 id 重复：{dup}")
        if not _get(scent, "hardware", "ovr_omara", "enabled", default=False) and "fallback" not in scent:
            report.warn("SCENT_NO_FALLBACK", "scent:/fallback", "未启用嗅觉硬件且未配置降级表达，气味将只能静默丢弃")

        player = modules.get("player_body") or {}
        for effect in player.get("status_effects") or []:
            ref = _get(effect, "feedback", "scent")
            if ref and ref not in scent_ids:
                report.error(
                    "SCENT_UNKNOWN_REF",
                    f"player_body:/status_effects/{_get(effect, 'id')}/feedback/scent",
                    f"状态效果引用了不存在的气味：{ref}",
                )

    taste = modules.get("taste")
    if taste:
        bindings = taste.get("food_bindings") or []
        seen_food: set[str] = set()
        for binding in bindings:
            food_id = _get(binding, "food_id")
            if food_id in seen_food:
                report.error("TASTE_DUPLICATE_FOOD", "taste:/food_bindings", f"食物味觉绑定重复：{food_id}")
            if isinstance(food_id, str):
                seen_food.add(food_id)
        if not _get(taste, "hardware", "vocktail", "enabled", default=False) and "fallback" not in taste:
            report.warn("TASTE_NO_FALLBACK", "taste:/fallback", "未启用味觉硬件且未配置降级表达，进食将缺少味觉反馈")

    audio = modules.get("audio")
    if audio and not (audio.get("material_sound_map") or {}):
        report.warn("AUDIO_NO_MATERIAL", "audio:/material_sound_map", "未配置材质音效，所有碰撞将回退默认音效")

    reverb_map = (audio or {}).get("scene_reverb_map") or {}
    if reverb_map:
        scene_ids = {item.get("id") for item in (modules.get("scenes") or {}).get("scenes", []) if isinstance(item, dict)}
        for scene_id in reverb_map:
            if scene_id not in scene_ids:
                report.error(
                    "AUDIO_UNKNOWN_SCENE",
                    f"audio:/scene_reverb_map/{scene_id}",
                    f"混响映射引用了不存在的场景：{scene_id}",
                )


def _check_sensory_bus(bus: dict[str, Any], modules: dict[str, Any], report: Report) -> None:
    """感官事件总线自洽性：同步窗口、通道模式、设备时延与路由可用性。"""
    channels = bus.get("channels") or {}
    sync_window = float(bus.get("sync_window_ms", 45))
    soft_window = float(bus.get("soft_sync_window_ms", 400))

    if sync_window > 80:
        report.warn(
            "BUS_SYNC_WINDOW_WIDE",
            "sensory_bus:/sync_window_ms",
            f"跨模态同步窗 {sync_window:g}ms 超过多感官整合研究给出的 80ms 上限，玩家可能察觉感官割裂",
        )

    seen_devices: dict[str, str] = {}
    for channel, conf in channels.items():
        if not isinstance(conf, dict):
            continue
        devices = [device for device in conf.get("devices") or [] if isinstance(device, dict)]
        enabled = [device for device in devices if device.get("enabled", True)]
        mode = conf.get("sync_mode", "hard")
        window = sync_window if mode == "hard" else soft_window

        if conf.get("enabled", True) and not enabled:
            report.warn(
                "BUS_CHANNEL_NO_DEVICE",
                f"sensory_bus:/channels/{channel}/devices",
                f"{channel} 通道已启用但没有可用设备，该通道事件会被总线丢弃",
            )
        latencies = [float(device.get("latency_ms", 0)) for device in enabled]
        if len(latencies) > 1 and max(latencies) - min(latencies) > window:
            report.warn(
                "BUS_SYNC_IMPOSSIBLE",
                f"sensory_bus:/channels/{channel}/devices",
                f"{channel} 通道设备时延跨度 {max(latencies) - min(latencies):g}ms 超出 {window:g}ms 同步窗，慢设备会被裁剪",
            )
        if channel in ("visual", "audio", "haptic") and mode != "hard":
            report.warn(
                "BUS_HARD_CHANNEL_SOFT",
                f"sensory_bus:/channels/{channel}/sync_mode",
                f"{channel} 属于高实时通道，建议使用 hard 同步以维持临场感",
            )
        if channel in ("scent", "taste") and mode != "soft":
            report.warn(
                "BUS_SOFT_CHANNEL_HARD",
                f"sensory_bus:/channels/{channel}/sync_mode",
                f"{channel} 外设扩散/刺激延迟高，使用 hard 同步会大量裁剪事件",
            )

        for device in devices:
            device_id = device.get("id")
            if device_id in seen_devices and seen_devices[device_id] != channel:
                report.error(
                    "BUS_DEVICE_AMBIGUOUS",
                    f"sensory_bus:/channels/{channel}/devices",
                    f"设备 {device_id} 同时挂在 {seen_devices[device_id]} 与 {channel} 通道，驱动指令会冲突",
                )
            if isinstance(device_id, str):
                seen_devices[device_id] = channel

    policy = bus.get("focus_policy") or {}
    if policy.get("enabled") and float(policy.get("off_focus_scale", 1.0)) >= 1.0:
        report.warn(
            "BUS_FOCUS_NO_EFFECT",
            "sensory_bus:/focus_policy/off_focus_scale",
            "焦点降级已开启但折扣为 1.0，非焦点事件不会降级",
        )

    for kind, channel_list in sorted((bus.get("routing") or {}).items()):
        for channel in channel_list or []:
            if channel not in modules:
                report.warn(
                    "BUS_ROUTE_WITHOUT_MODULE",
                    f"sensory_bus:/routing/{kind}",
                    f"路由把 {kind} 派发到 {channel}，但世界包没有 {channel} 感官模块，会回退到引擎默认输出",
                )


def _check_scenes(
    scenes: dict[str, Any],
    modules: dict[str, Any],
    manifest: dict[str, Any],
    report: Report,
) -> None:
    """场景层自洽性：空间不重叠、群落可解析、UEF 与世界物理不矛盾、危害覆盖极端参数。"""
    items = [item for item in scenes.get("scenes") or [] if isinstance(item, dict)]
    for dup in _duplicate_ids(items):
        report.error("SCENE_DUPLICATE_ID", "scenes:/scenes", f"场景 id 重复：{dup}")

    world_bounds = _get(manifest, "terrain", "world_bounds_chunks", default=[]) or []
    boxes: list[tuple[dict[str, Any], list[int], list[int]]] = []
    for item in items:
        sid = item.get("id")
        bounds = item.get("bounds") or {}
        low, high = bounds.get("min_chunk"), bounds.get("max_chunk")
        if not (isinstance(low, list) and isinstance(high, list) and len(low) == 3 and len(high) == 3):
            continue
        path = f"scenes:/scenes/{sid}/bounds"
        if any(start > end for start, end in zip(low, high)):
            report.error("SCENE_BOUNDS_INVALID", path, f"包围盒下界大于上界：{low} > {high}")
            continue
        if len(world_bounds) == 3:
            for axis, end in enumerate(high):
                if end >= world_bounds[axis]:
                    report.error(
                        "SCENE_BOUNDS_OUTSIDE",
                        path,
                        f"第 {axis} 轴终点 {end} 超出世界范围 0~{world_bounds[axis] - 1}",
                    )
        boxes.append((item, low, high))

    for index, (left, left_low, left_high) in enumerate(boxes):
        for right, right_low, right_high in boxes[index + 1 :]:
            if all(left_low[k] <= right_high[k] and right_low[k] <= left_high[k] for k in range(3)):
                report.error(
                    "SCENE_BOUNDS_OVERLAP",
                    f"scenes:/scenes/{left.get('id')}/bounds",
                    f"与场景 {right.get('id')} 的包围盒重叠，同一空间不能属于两个场景",
                )

    physics = modules.get("physics") or {}
    ecology = modules.get("ecology") or {}
    scent = modules.get("scent") or {}
    biome_ids = {
        biome.get("id")
        for biome in _get(ecology, "ecosystem", "biomes", default=[]) or []
        if isinstance(biome, dict)
    }
    scent_ids = {item.get("id") for item in scent.get("scent_lib") or [] if isinstance(item, dict)}

    world_gravity = physics.get("gravity")
    world_density = physics.get("air_density")
    world_temp = _get(physics, "thermal", "ambient_temperature_c")

    for item in items:
        sid = item.get("id")
        uef = item.get("uef") or {}
        medium = uef.get("medium") or {}
        energy = uef.get("energy") or {}
        resources = uef.get("resources") or {}
        kinds = {hazard.get("kind") for hazard in item.get("hazards") or [] if isinstance(hazard, dict)}

        biome = item.get("biome_ref")
        if biome and biome_ids and biome not in biome_ids:
            report.error("SCENE_UNKNOWN_BIOME", f"scenes:/scenes/{sid}/biome_ref", f"未知群落：{biome}")
        for ref in (item.get("sensory") or {}).get("scent_refs") or []:
            if scent and ref not in scent_ids:
                report.error("SCENE_UNKNOWN_SCENT", f"scenes:/scenes/{sid}/sensory/scent_refs", f"气味库中不存在：{ref}")

        gravity = uef.get("gravity")
        if isinstance(gravity, (int, float)) and isinstance(world_gravity, (int, float)):
            if abs(gravity - world_gravity) > 2.5:
                report.warn(
                    "SCENE_GRAVITY_MISMATCH",
                    f"scenes:/scenes/{sid}/uef/gravity",
                    f"场景重力 {gravity} 与世界 {world_gravity} 相差超过 2.5 m/s²，需确认是否为跨星球区域",
                )
        if medium.get("kind") == "vacuum" and isinstance(world_density, (int, float)) and world_density > 0.2:
            report.warn(
                "SCENE_MEDIUM_CONFLICT",
                f"scenes:/scenes/{sid}/uef/medium",
                f"真空场景与世界空气密度 {world_density} 冲突，物理内核会按全球大气求解",
            )
        temperature = energy.get("ambient_temperature_c")
        if isinstance(temperature, (int, float)) and isinstance(world_temp, (int, float)):
            if abs(temperature - world_temp) > 20:
                report.warn(
                    "SCENE_TEMPERATURE_MISMATCH",
                    f"scenes:/scenes/{sid}/uef/energy",
                    f"场景温度 {temperature}℃ 与世界平均 {world_temp}℃ 相差超过 20℃，需局部热源或隔热设定",
                )
        oxygen = resources.get("breathable_oxygen")
        medium_kind = medium.get("kind")
        if isinstance(oxygen, (int, float)) and oxygen < 0.5 and "hypoxia" not in kinds:
            if medium_kind not in ("water", "seawater"):
                report.warn("SCENE_HAZARD_MISSING", f"scenes:/scenes/{sid}/hazards", f"氧含量 {oxygen} 偏低，但未声明 hypoxia 危害")
        if medium_kind in ("water", "seawater") and "drowning" not in kinds:
            report.warn("SCENE_HAZARD_MISSING", f"scenes:/scenes/{sid}/hazards", "水域场景未声明 drowning 危害")
        if isinstance(temperature, (int, float)) and temperature < -10 and "hypothermia" not in kinds:
            report.warn("SCENE_HAZARD_MISSING", f"scenes:/scenes/{sid}/hazards", f"环境温度 {temperature}℃，但未声明 hypothermia 危害")
        if isinstance(temperature, (int, float)) and temperature > 45 and "hyperthermia" not in kinds:
            report.warn("SCENE_HAZARD_MISSING", f"scenes:/scenes/{sid}/hazards", f"环境温度 {temperature}℃，但未声明 hyperthermia 危害")

    scene_ids = {item.get("id") for item in items}
    npc_agent = modules.get("npc_agent") or {}
    for agent in npc_agent.get("npc_roster") or []:
        if not isinstance(agent, dict):
            continue
        scene_id = agent.get("scene_id")
        if scene_id and scene_id not in scene_ids:
            report.error(
                "NPC_UNKNOWN_SCENE",
                f"npc_agent:/npc_roster/{agent.get('id')}/scene_id",
                f"NPC 常驻场景不存在：{scene_id}",
            )


def _check_narrative(narrative: dict[str, Any], modules: dict[str, Any], report: Report) -> None:
    """剧情层自洽性：三层引用可解析、阶层份额合理、因果律完整、结构压力可观察。"""
    nations = [item for item in narrative.get("nations") or [] if isinstance(item, dict)]
    classes = [item for item in narrative.get("classes") or [] if isinstance(item, dict)]
    tensions = [item for item in narrative.get("tensions") or [] if isinstance(item, dict)]
    arcs = [item for item in narrative.get("arcs") or [] if isinstance(item, dict)]
    chronicle = [item for item in narrative.get("chronicle") or [] if isinstance(item, dict)]
    rules = [item for item in narrative.get("causal_rules") or [] if isinstance(item, dict)]

    for label, items in (
        ("nations", nations),
        ("classes", classes),
        ("tensions", tensions),
        ("arcs", arcs),
        ("chronicle", chronicle),
        ("causal_rules", rules),
    ):
        for dup in _duplicate_ids(items):
            report.error("NARRATIVE_DUPLICATE_ID", f"narrative:/{label}", f"{label} 中 id 重复：{dup}")

    nation_ids = {item.get("id") for item in nations}
    class_ids = {item.get("id") for item in classes}
    rule_ids = {item.get("id") for item in rules}
    tension_ids = {item.get("id") for item in tensions}
    arc_ids = {item.get("id") for item in arcs}
    npc_ids = {item.get("id") for item in (modules.get("npc_agent") or {}).get("npc_roster") or []}
    scene_ids = {item.get("id") for item in (modules.get("scenes") or {}).get("scenes") or []}
    quests = [item for item in narrative.get("quests") or [] if isinstance(item, dict)]

    for dup in _duplicate_ids(quests):
        report.error("NARRATIVE_DUPLICATE_ID", "narrative:/quests", f"quests 中 id 重复：{dup}")
    for index, quest in enumerate(quests):
        scene_ref = quest.get("scene_id")
        if scene_ids and scene_ref not in scene_ids:
            report.error(
                "QUEST_UNKNOWN_SCENE",
                f"narrative:/quests/{index}/scene_id",
                f"任务 {quest.get('id')} 指向不存在的场景：{scene_ref}",
            )
        tension_ref = quest.get("tension_id")
        if tension_ref is not None and tension_ref not in tension_ids:
            report.error(
                "QUEST_UNKNOWN_TENSION",
                f"narrative:/quests/{index}/tension_id",
                f"任务 {quest.get('id')} 指向不存在的矛盾：{tension_ref}",
            )

    for index, nation in enumerate(nations):
        for scene_id in nation.get("territory_scene_refs") or []:
            if scene_ids and scene_id not in scene_ids:
                report.error(
                    "NARRATIVE_UNKNOWN_SCENE",
                    f"narrative:/nations/{index}/territory_scene_refs",
                    f"国家 {nation.get('id')} 的领土场景不存在：{scene_id}",
                )
        if float(nation.get("stability", 1.0)) < 0.3:
            report.warn(
                "NARRATIVE_FRAGILE_STATE",
                f"narrative:/nations/{index}/stability",
                f"国家 {nation.get('id')} 稳定性过低（<0.3），剧情会自然走向动荡",
            )

    shares: dict[str, float] = {}
    incomes: dict[str, list[float]] = {}
    for index, klass in enumerate(classes):
        nation_id = klass.get("nation_id")
        if nation_id not in nation_ids:
            report.error(
                "NARRATIVE_UNKNOWN_NATION",
                f"narrative:/classes/{index}/nation_id",
                f"阶层 {klass.get('id')} 所属国家不存在：{nation_id}",
            )
        else:
            shares[nation_id] = shares.get(nation_id, 0.0) + float(klass.get("share", 0.0))
            incomes.setdefault(nation_id, []).append(float(klass.get("income_index", 0.0)))

    for nation_id, total in shares.items():
        if total > 1.05:
            report.warn(
                "NARRATIVE_CLASS_SHARE_OVERFLOW",
                f"narrative:/classes",
                f"国家 {nation_id} 的阶层份额之和为 {round(total, 3)}，超过 1",
            )
        elif total < 0.95:
            report.warn(
                "NARRATIVE_CLASS_SHARE_GAP",
                f"narrative:/classes",
                f"国家 {nation_id} 的阶层份额之和为 {round(total, 3)}，未覆盖全部人口",
            )

    for nation_id, values in incomes.items():
        if len(values) > 1 and max(values) - min(values) >= 6:
            report.warn(
                "NARRATIVE_INEQUALITY",
                "narrative:/classes",
                f"国家 {nation_id} 的阶层收入差达 {round(max(values) - min(values), 2)}，具备革命或起义的结构条件",
            )

    if classes and all(float(item.get("mobility", 0.0)) <= 0.1 for item in classes):
        report.warn("NARRATIVE_RIGID_CLASSES", "narrative:/classes", "所有阶层流动性均极低，社会结构接近固化")

    for index, tension in enumerate(tensions):
        for party in tension.get("parties") or []:
            kind, ref = party.get("kind"), party.get("ref")
            pool = {"nation": nation_ids, "class": class_ids, "npc": npc_ids}.get(kind)
            if pool is not None and ref not in pool:
                report.error(
                    "NARRATIVE_UNKNOWN_PARTY",
                    f"narrative:/tensions/{index}/parties",
                    f"矛盾 {tension.get('id')} 的 {kind} 方不存在：{ref}",
                )

    for index, arc in enumerate(arcs):
        if arc.get("npc_ref") not in npc_ids:
            report.error(
                "NARRATIVE_UNKNOWN_NPC",
                f"narrative:/arcs/{index}/npc_ref",
                f"个体线 {arc.get('id')} 的 NPC 不存在：{arc.get('npc_ref')}",
            )
        if arc.get("nation_ref") and arc["nation_ref"] not in nation_ids:
            report.error("NARRATIVE_UNKNOWN_NATION", f"narrative:/arcs/{index}/nation_ref", f"个体线国家引用不存在：{arc['nation_ref']}")
        if arc.get("class_ref") and arc["class_ref"] not in class_ids:
            report.error("NARRATIVE_UNKNOWN_CLASS", f"narrative:/arcs/{index}/class_ref", f"个体线阶层引用不存在：{arc['class_ref']}")

    known_refs = nation_ids | class_ids | npc_ids
    for index, entry in enumerate(chronicle):
        for participant in entry.get("participants") or []:
            if participant not in known_refs:
                report.error(
                    "NARRATIVE_UNKNOWN_PARTICIPANT",
                    f"narrative:/chronicle/{index}/participants",
                    f"编年史事件 {entry.get('id')} 的参与者不存在：{participant}",
                )
        if entry.get("tension_ref") and entry["tension_ref"] not in tension_ids:
            report.error("NARRATIVE_UNKNOWN_TENSION", f"narrative:/chronicle/{index}/tension_ref", f"编年史引用的矛盾不存在：{entry['tension_ref']}")
        if entry.get("arc_ref") and entry["arc_ref"] not in arc_ids:
            report.error("NARRATIVE_UNKNOWN_ARC", f"narrative:/chronicle/{index}/arc_ref", f"编年史引用的个体线不存在：{entry['arc_ref']}")

    generation = narrative.get("generation") or {}
    for rule_id in generation.get("must_respect") or []:
        if rule_id not in rule_ids:
            report.error(
                "NARRATIVE_UNKNOWN_RULE",
                "narrative:/generation/must_respect",
                f"AI 生成契约引用了不存在的因果律：{rule_id}",
            )
    if not rules:
        report.warn("NARRATIVE_NO_RULES", "narrative:/causal_rules", "缺少因果律，AI 剧情会退化为随机事件")


def _check_cross_module(modules: dict[str, Any], report: Report) -> None:
    physics, player = modules.get("physics"), modules.get("player_body")
    ambient = _get(physics, "thermal", "ambient_temperature_c")
    comfort = _get(player, "vitals", "temperature", "comfort_ambient_range_c", default=[])
    if isinstance(ambient, (int, float)) and isinstance(comfort, list) and len(comfort) == 2:
        if not comfort[0] <= ambient <= comfort[1]:
            report.warn(
                "CROSS_AMBIENT_OUTSIDE_COMFORT",
                "physics:/thermal/ambient_temperature_c",
                f"世界平均环境温度 {ambient}℃ 不在玩家舒适区间 {comfort} 内，出生即进入温度负面状态",
            )

    ecology, npc = modules.get("ecology"), modules.get("npc_agent")
    species_count = len(_get(ecology, "species", default=[]) or [])
    goods_count = len(_get(npc, "economy", "goods", default=[]) or [])
    if species_count and goods_count and goods_count < species_count:
        report.warn("CROSS_ECONOMY_THIN", "npc_agent:/economy/goods", "商品种类少于物种数量，经济系统难以反映生态产出")

    visual = modules.get("visual")
    effects = _get(player, "status_effects", default=[]) or []
    vfx_map = _get(visual, "status_vfx_map", default={}) or {}
    if visual is not None and effects:
        unmapped = sorted(
            effect.get("id")
            for effect in effects
            if isinstance(effect, dict) and effect.get("id") not in vfx_map
        )
        if unmapped:
            report.warn(
                "CROSS_VFX_UNMAPPED",
                "visual:/status_vfx_map",
                f"以下生理状态缺少视觉映射，将由引擎回退默认 VFX：{', '.join(unmapped)}",
            )

        known = {effect.get("id") for effect in effects if isinstance(effect, dict)}
        unknown = sorted(key for key in vfx_map if key not in known)
        if unknown:
            report.warn(
                "CROSS_VFX_UNKNOWN",
                "visual:/status_vfx_map",
                f"以下视觉映射没有对应的生理状态，可能已失效：{', '.join(unknown)}",
            )
        for effect in effects:
            if not isinstance(effect, dict):
                continue
            effect_id = effect.get("id")
            feedback_vfx = _get(effect, "feedback", "vfx")
            mapped_vfx = vfx_map.get(effect_id)
            if feedback_vfx and mapped_vfx and feedback_vfx != mapped_vfx:
                report.warn(
                    "CROSS_VFX_MISMATCH",
                    f"player_body:/status_effects/{effect_id}/feedback/vfx",
                    f"生理反馈使用 {feedback_vfx}，视觉映射为 {mapped_vfx}，两处不一致",
                )

    location_map = _get(npc, "location_scene_map", default={}) or {}
    if location_map:
        scene_ids = {
            scene.get("id")
            for scene in _get(modules.get("scenes"), "scenes", default=[]) or []
            if isinstance(scene, dict) and scene.get("id")
        }
        for location, scene_id in sorted(location_map.items()):
            if scene_id not in scene_ids:
                report.error(
                    "NPC_LOCATION_UNKNOWN_SCENE",
                    f"npc_agent:/location_scene_map/{location}",
                    f"日程地点 {location} 映射到不存在的场景：{scene_id}",
                )
