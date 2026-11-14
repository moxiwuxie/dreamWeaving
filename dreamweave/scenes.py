"""场景库与统一环境场（UEF）。

现实里从城市到深海再到太空，看似毫无共性；但它们都可以用同一组六轴参数描述：

    1 重力场 gravity        —— 决定坠落、跳跃、抛射、建筑承重
    2 介质   medium         —— 决定声音、浮力、燃烧、传播衰减
    3 能量   energy         —— 决定温度、光照、辐射、昼夜
    4 地形   terrain        —— 决定可达性、视野、建筑形态
    5 资源   resources      —— 决定承载力、经济、生存压力
    6 社会   society        —— 决定人口密度、建成度、秩序

场景不是新的模拟规则，只是这六轴上的一组坐标；物理/生态/五感配置由坐标推导。
新增场景 = 填一行坐标，不需要改内核。
"""

from __future__ import annotations

import copy
from typing import Any

CATEGORIES = ("natural", "built", "transition", "extraterrestrial", "fantasy")

# (密度 kg/m³, 压力 kPa, 声速 m/s, 粘度 Pa·s)
_MEDIUM_DEFAULTS: dict[str, tuple[float, float, float, float]] = {
    "vacuum": (0.0, 0.0, 0.0, 0.0),
    "thin_air": (0.02, 0.6, 240.0, 1e-5),
    "air": (1.225, 101.3, 343.0, 1.8e-5),
    "water": (1000.0, 101.3, 1480.0, 0.001),
    "seawater": (1025.0, 101.3, 1530.0, 0.0011),
    "soil": (1800.0, 300.0, 3000.0, 1000.0),
    "ice": (917.0, 101.3, 3800.0, 1000000.0),
    "plasma": (0.5, 1000.0, 600.0, 1e-5),
}


def _hz(kind: str, severity: float, note: str | None = None) -> dict[str, Any]:
    hazard: dict[str, Any] = {"kind": kind, "severity": severity}
    if note:
        hazard["note"] = note
    return hazard


def _scene(
    scene_id: str,
    name: str,
    category: str,
    *,
    medium: str = "air",
    gravity: float = -9.81,
    temp: float = 15.0,
    solar: float = 1.0,
    radiation: float = 1.0,
    gradient: float = -0.0065,
    elevation: float = 0.0,
    relief: float = 50.0,
    slope: float = 2.0,
    enclosure: float = 0.0,
    water: float = 0.6,
    biomass: float = 0.6,
    mineral: float = 0.4,
    oxygen: float = 1.0,
    pop: float = 10.0,
    built: float = 0.05,
    law: float = 0.3,
    preset: str | None = None,
    biome: str | None = None,
    hazards: tuple[dict[str, Any], ...] = (),
    reverb: str | None = None,
    scents: tuple[str, ...] = (),
    light_ev: float | None = None,
    density: float | None = None,
    pressure: float | None = None,
    sound: float | None = None,
    viscosity: float | None = None,
) -> dict[str, Any]:
    density_default, pressure_default, sound_default, viscosity_default = _MEDIUM_DEFAULTS[medium]
    scene: dict[str, Any] = {
        "id": scene_id,
        "name": name,
        "category": category,
        "uef": {
            "gravity": gravity,
            "medium": {
                "kind": medium,
                "density_kg_m3": density if density is not None else density_default,
                "pressure_kpa": pressure if pressure is not None else pressure_default,
                "sound_speed_mps": sound if sound is not None else sound_default,
                "viscosity_pa_s": viscosity if viscosity is not None else viscosity_default,
            },
            "energy": {
                "ambient_temperature_c": temp,
                "solar_irradiance_scale": solar,
                "radiation_scale": radiation,
                "thermal_gradient_c_per_m": gradient,
            },
            "terrain": {
                "elevation_m": elevation,
                "relief_m": relief,
                "slope_deg": slope,
                "enclosure": enclosure,
            },
            "resources": {
                "water_availability": water,
                "biomass": biomass,
                "mineral_richness": mineral,
                "breathable_oxygen": oxygen,
            },
            "society": {
                "population_density_per_km2": pop,
                "built_density": built,
                "law_presence": law,
            },
        },
    }
    if preset:
        scene["physics_preset"] = preset
    if biome:
        scene["biome_ref"] = biome
    if hazards:
        scene["hazards"] = list(hazards)
    if reverb or scents or light_ev is not None:
        sensory: dict[str, Any] = {}
        if reverb:
            sensory["reverb_preset"] = reverb
        if scents:
            sensory["scent_refs"] = list(scents)
        if light_ev is not None:
            sensory["ambient_light_ev"] = light_ev
        scene["sensory"] = sensory
    return scene


SCENES: list[dict[str, Any]] = [
    # ---------- 自然地貌 ----------
    _scene("alpine_peak", "高山之巅", "natural", elevation=4500, relief=2000, slope=35, temp=-10, solar=1.0,
           water=0.3, biomass=0.05, mineral=0.5, oxygen=0.55, pop=0, built=0, law=0,
           hazards=(_hz("hypoxia", 0.7), _hz("hypothermia", 0.6), _hz("fall", 0.8), _hz("radiation", 0.5))),
    _scene("mountain_range", "山地", "natural", elevation=1800, relief=1200, slope=25, temp=5,
           water=0.7, biomass=0.5, mineral=0.7, pop=5, built=0.02, law=0.1,
           hazards=(_hz("fall", 0.5), _hz("hypothermia", 0.3))),
    _scene("hills", "丘陵", "natural", elevation=300, relief=200, slope=12, temp=14,
           water=0.7, biomass=0.7, mineral=0.4, pop=30, built=0.08),
    _scene("plateau", "高原", "natural", elevation=4000, relief=400, slope=5, temp=-2, solar=1.0,
           water=0.35, biomass=0.25, oxygen=0.6, pop=8, built=0.05, law=0.2,
           hazards=(_hz("hypoxia", 0.5), _hz("radiation", 0.4), _hz("hypothermia", 0.4))),
    _scene("plains", "平原", "natural", elevation=50, relief=15, slope=1, temp=15,
           water=0.8, biomass=0.85, oxygen=1.0, pop=40, built=0.1),
    _scene("grassland", "草原", "natural", elevation=800, relief=60, slope=3, temp=12,
           water=0.5, biomass=0.75, pop=5, built=0.02),
    _scene("forest", "森林", "natural", elevation=400, relief=120, slope=8, temp=13, solar=0.7,
           enclosure=0.2, water=0.85, biomass=1.0, pop=6, built=0.02, reverb="forest",
           scents=("scent_pine",)),
    _scene("rainforest", "雨林", "natural", elevation=200, relief=80, slope=5, temp=27,
           water=1.0, biomass=1.0, pop=10, built=0.03, reverb="forest", scents=("scent_pine",)),
    _scene("wetland", "湿地", "natural", elevation=2, relief=3, slope=0.5, temp=16,
           water=1.0, biomass=0.95, pop=3, built=0.01, hazards=(_hz("drowning", 0.4),)),
    _scene("swamp", "沼泽", "natural", elevation=1, relief=2, temp=20, enclosure=0.2,
           water=1.0, biomass=0.9, oxygen=0.95, pop=2, built=0, reverb="forest",
           hazards=(_hz("drowning", 0.6), _hz("toxic", 0.5), _hz("visibility", 0.4))),
    _scene("desert", "沙漠", "natural", elevation=600, relief=100, slope=4, temp=35, solar=1.0,
           water=0.02, biomass=0.03, mineral=0.4, pop=1, built=0.02,
           hazards=(_hz("dehydration", 0.9), _hz("hyperthermia", 0.8), _hz("visibility", 0.5))),
    _scene("gobi", "戈壁", "natural", elevation=1200, relief=150, slope=3, temp=20,
           water=0.05, biomass=0.05, mineral=0.6, pop=0.5, built=0,
           hazards=(_hz("dehydration", 0.7), _hz("visibility", 0.4))),
    _scene("canyon", "峡谷", "natural", elevation=900, relief=600, slope=40, temp=18, enclosure=0.3,
           water=0.3, biomass=0.2, mineral=0.5, pop=2, built=0.02,
           hazards=(_hz("fall", 0.7), _hz("dehydration", 0.4))),
    _scene("karst_cave", "喀斯特溶洞", "natural", elevation=300, relief=100, slope=20, enclosure=1.0,
           temp=14, solar=0.0, water=0.6, biomass=0.1, mineral=0.5, oxygen=0.9, pop=0, built=0, law=0,
           reverb="cave", hazards=(_hz("visibility", 0.8), _hz("fall", 0.4), _hz("toxic", 0.3))),
    _scene("glacier", "冰川", "natural", elevation=2500, relief=300, slope=10, temp=-15, solar=1.0,
           water=0.5, biomass=0.02, pop=0, built=0, law=0,
           hazards=(_hz("hypothermia", 0.9), _hz("fall", 0.6), _hz("visibility", 0.4))),
    _scene("tundra", "极地苔原", "natural", elevation=100, relief=40, slope=2, temp=-12, solar=0.8,
           water=0.5, biomass=0.15, pop=0.2, built=0.01, hazards=(_hz("hypothermia", 0.8),)),
    _scene("volcano", "火山", "natural", elevation=2000, relief=1500, slope=30, temp=60, solar=0.9,
           radiation=0.6, water=0.1, biomass=0.02, mineral=0.9, pop=1, built=0.05,
           hazards=(_hz("hyperthermia", 0.9), _hz("toxic", 0.8), _hz("fall", 0.7), _hz("visibility", 0.7))),
    _scene("river", "河流", "natural", elevation=200, relief=20, slope=1, temp=16,
           water=1.0, biomass=0.8, pop=20, built=0.1,
           hazards=(_hz("drowning", 0.5),)),
    _scene("lake", "湖泊", "natural", medium="water", elevation=400, relief=20, slope=0, temp=14,
           water=1.0, biomass=0.7, oxygen=0.0, pop=15, built=0.08,
           hazards=(_hz("drowning", 0.6),)),
    _scene("coast", "海岸", "natural", elevation=3, relief=10, slope=2, temp=18,
           water=0.9, biomass=0.8, pop=50, built=0.2, scents=("scent_pine",)),
    _scene("ocean", "海洋", "natural", medium="seawater", elevation=-100, pressure=500, temp=12,
           solar=0.6, water=1.0, biomass=0.5, oxygen=0.0, pop=0, built=0,
           hazards=(_hz("drowning", 0.9), _hz("pressure", 0.4))),
    _scene("coral_reef", "珊瑚礁", "natural", medium="seawater", elevation=-20, pressure=300, temp=26,
           solar=0.9, water=1.0, biomass=1.0, mineral=0.2, oxygen=0.0, pop=0, built=0,
           hazards=(_hz("drowning", 0.8), _hz("hostile_fauna", 0.4))),
    _scene("hydrothermal_vent", "海底热泉", "natural", medium="seawater", elevation=-2500, pressure=25000,
           temp=90, solar=0.0, radiation=0.3, water=1.0, biomass=0.3, mineral=1.0, oxygen=0.0,
           pop=0, built=0,
           hazards=(_hz("pressure", 0.9), _hz("hyperthermia", 0.7), _hz("toxic", 0.7), _hz("visibility", 0.8))),
    _scene("abyssal_plain", "深海平原", "natural", medium="seawater", elevation=-4000, pressure=40000,
           temp=2, solar=0.0, water=1.0, biomass=0.05, oxygen=0.0, pop=0, built=0,
           hazards=(_hz("pressure", 1.0), _hz("isolation", 0.8), _hz("visibility", 0.9))),
    _scene("trench", "海沟", "natural", medium="seawater", elevation=-10000, pressure=100000, temp=1,
           solar=0.0, water=1.0, biomass=0.02, oxygen=0.0, pop=0, built=0,
           hazards=(_hz("pressure", 1.0), _hz("isolation", 0.9), _hz("visibility", 1.0))),
    _scene("island", "岛屿", "natural", elevation=60, relief=80, slope=6, temp=22,
           water=0.7, biomass=0.8, pop=30, built=0.15, hazards=(_hz("isolation", 0.3),)),
    _scene("archipelago", "群岛", "natural", elevation=30, relief=60, slope=4, temp=23,
           water=0.8, biomass=0.85, pop=25, built=0.12, hazards=(_hz("isolation", 0.4),)),
    _scene("underground_cavern", "地下洞穴", "natural", elevation=-300, relief=40, slope=10, enclosure=1.0,
           temp=12, solar=0.0, water=0.4, biomass=0.05, mineral=0.6, oxygen=0.85, pop=0, built=0, law=0,
           reverb="cave", hazards=(_hz("visibility", 0.9), _hz("fall", 0.5), _hz("isolation", 0.7))),
    _scene("deep_crust", "地壳深处", "natural", medium="soil", elevation=-8000, relief=200, slope=5,
           enclosure=1.0, temp=300, solar=0.0, radiation=0.6, water=0.05, biomass=0, mineral=1.0,
           pop=0, built=0, law=0,
           hazards=(_hz("pressure", 1.0), _hz("hyperthermia", 1.0), _hz("toxic", 0.8), _hz("visibility", 1.0))),

    # ---------- 空中与地外 ----------
    _scene("sky_layer", "云层之上", "natural", medium="thin_air", elevation=8000, pressure=35, temp=-30,
           solar=1.0, radiation=0.7, water=0.4, biomass=0, oxygen=0.3, pop=0, built=0,
           hazards=(_hz("hypoxia", 0.9), _hz("hypothermia", 0.9), _hz("radiation", 0.6))),
    _scene("low_orbit", "近地轨道", "extraterrestrial", medium="vacuum", gravity=-8.7, elevation=400000,
           temp=-20, solar=1.0, radiation=0.9, water=0, biomass=0, oxygen=0, pop=0, built=0, law=0,
           preset="zero_g",
           hazards=(_hz("hypoxia", 1.0), _hz("radiation", 0.9), _hz("isolation", 0.7))),
    _scene("deep_space", "深空", "extraterrestrial", medium="vacuum", gravity=0, elevation=0, temp=-270,
           solar=0.1, radiation=1.0, water=0, biomass=0, mineral=0.1, oxygen=0, pop=0, built=0, law=0,
           preset="zero_g",
           hazards=(_hz("hypoxia", 1.0), _hz("radiation", 1.0), _hz("isolation", 1.0), _hz("hypothermia", 0.9))),
    _scene("lunar_surface", "月球表面", "extraterrestrial", medium="vacuum", gravity=-1.62, relief=300,
           slope=8, temp=-20, solar=1.0, radiation=0.9, water=0.02, biomass=0, mineral=0.7, oxygen=0,
           pop=0, built=0, law=0, preset="moon",
           hazards=(_hz("hypoxia", 1.0), _hz("radiation", 0.9), _hz("fall", 0.4))),
    _scene("mars_surface", "火星地表", "extraterrestrial", medium="thin_air", gravity=-3.71, relief=500,
           slope=6, temp=-60, solar=0.6, radiation=0.8, water=0.05, biomass=0, mineral=0.8, oxygen=0.01,
           pop=0, built=0, law=0, preset="mars",
           hazards=(_hz("hypoxia", 0.9), _hz("hypothermia", 0.9), _hz("radiation", 0.6))),
    _scene("gas_giant_atmosphere", "气态巨行星大气", "extraterrestrial", medium="plasma", gravity=-24.8,
           temp=-100, pressure=1000, solar=0.4, radiation=1.0, water=0, biomass=0, oxygen=0,
           pop=0, built=0, law=0,
           hazards=(_hz("pressure", 1.0), _hz("toxic", 1.0), _hz("visibility", 1.0))),

    # ---------- 建成环境 ----------
    _scene("city_core", "城市中心", "built", temp=18, solar=0.8, relief=5, slope=1, water=0.7,
           biomass=0.1, mineral=0.2, pop=12000, built=0.95, law=0.9,
           hazards=(_hz("toxic", 0.2),)),
    _scene("suburb", "城郊", "built", temp=17, solar=0.9, relief=10, water=0.7, biomass=0.4,
           pop=2000, built=0.5, law=0.7),
    _scene("rural_village", "农村", "built", temp=16, relief=20, water=0.8, biomass=0.8,
           pop=200, built=0.2, law=0.5),
    _scene("farmland", "农田", "built", temp=16, relief=10, slope=1, water=0.85, biomass=0.85,
           pop=60, built=0.15, law=0.5),
    _scene("pasture", "牧场", "built", temp=14, relief=30, water=0.6, biomass=0.8,
           pop=15, built=0.08, law=0.4),
    _scene("fishing_village", "渔村", "built", temp=17, relief=8, water=1.0, biomass=0.7,
           pop=300, built=0.3, law=0.5, scents=("scent_pine",)),
    _scene("harbor", "港口", "built", temp=18, relief=5, water=1.0, biomass=0.4,
           pop=800, built=0.6, law=0.6),
    _scene("industrial_zone", "工业区", "built", temp=22, solar=0.6, relief=8, water=0.5, biomass=0.05,
           mineral=0.5, pop=1500, built=0.9, law=0.6,
           hazards=(_hz("toxic", 0.6), _hz("visibility", 0.3))),
    _scene("mine", "矿区", "built", elevation=500, relief=100, slope=10, enclosure=0.3, temp=12,
           solar=0.1, water=0.4, biomass=0.05, mineral=1.0, pop=200, built=0.5, law=0.5,
           reverb="cave", hazards=(_hz("toxic", 0.5), _hz("fall", 0.6), _hz("visibility", 0.6))),
    _scene("military_base", "军事基地", "built", temp=17, relief=5, water=0.6, biomass=0.2,
           pop=600, built=0.7, law=1.0),
    _scene("sacred_site", "宗教圣地", "built", temp=16, relief=15, water=0.6, biomass=0.5,
           pop=150, built=0.4, law=0.95, reverb="cathedral"),
    _scene("ruins", "废墟", "built", temp=16, relief=30, enclosure=0.4, water=0.4, biomass=0.3,
           pop=5, built=0.4, law=0.1, reverb="cave",
           hazards=(_hz("fall", 0.4), _hz("toxic", 0.2))),
    _scene("underground_city", "地下城", "built", elevation=-400, relief=20, enclosure=1.0, temp=22,
           solar=0.0, water=0.5, biomass=0.1, mineral=0.4, oxygen=0.9, pop=5000, built=0.95, law=0.8,
           reverb="cave", hazards=(_hz("isolation", 0.5), _hz("visibility", 0.4))),

    # ---------- 过渡 / 人工地外据点 ----------
    _scene("tunnel", "隧道", "transition", enclosure=1.0, temp=16, solar=0.0, water=0.3, biomass=0.05,
           pop=0, built=1.0, law=0.4, reverb="cave", hazards=(_hz("visibility", 0.6),)),
    _scene("offshore_platform", "海上平台", "transition", elevation=30, relief=2, temp=18, water=0.9,
           biomass=0.2, pop=50, built=0.7, law=0.5, hazards=(_hz("fall", 0.6), _hz("isolation", 0.5))),
    _scene("underwater_habitat", "水下基地", "transition", enclosure=1.0, elevation=-500, pressure=5000,
           temp=20, solar=0.0, water=0.4, biomass=0.2, oxygen=0.9, pop=40, built=0.85, law=0.7,
           reverb="small_room", hazards=(_hz("drowning", 0.6), _hz("pressure", 0.8), _hz("isolation", 0.7))),
    _scene("space_station", "太空站", "transition", medium="vacuum", gravity=0, enclosure=1.0,
           temp=22, solar=1.0, radiation=0.7, water=0.3, biomass=0.1, oxygen=0.9, pop=80, built=0.9,
           law=0.8, preset="zero_g", reverb="small_room",
           hazards=(_hz("hypoxia", 0.7), _hz("radiation", 0.6), _hz("isolation", 0.8))),
    _scene("lunar_base", "月球基地", "transition", medium="vacuum", gravity=-1.62, enclosure=1.0,
           temp=22, solar=1.0, radiation=0.6, water=0.3, biomass=0.1, oxygen=0.9, pop=60, built=0.85,
           law=0.8, preset="moon", reverb="small_room",
           hazards=(_hz("hypoxia", 0.7), _hz("radiation", 0.5), _hz("isolation", 0.9))),

    # ---------- 幻想 ----------
    _scene("floating_island", "浮空岛", "fantasy", gravity=-4.0, elevation=1200, relief=200, slope=15,
           temp=16, solar=0.9, water=0.5, biomass=0.6, mineral=0.3, pop=12, built=0.1, law=0.4,
           preset="fantasy", hazards=(_hz("fall", 0.9), _hz("isolation", 0.5))),
    _scene("dreamscape", "梦境空间", "fantasy", gravity=-1.0, enclosure=0.6, temp=20, solar=0.3,
           water=0.2, biomass=0.2, mineral=0.1, pop=0, built=0.05, law=0.1, preset="fantasy",
           hazards=(_hz("isolation", 0.9), _hz("visibility", 0.6))),
]

SCENE_INDEX: dict[str, dict[str, Any]] = {scene["id"]: scene for scene in SCENES}


def list_scenes(category: str | None = None) -> list[dict[str, Any]]:
    return [
        {"id": scene["id"], "name": scene["name"], "category": scene["category"]}
        for scene in SCENES
        if category is None or scene["category"] == category
    ]


def get_scene(scene_id: str) -> dict[str, Any]:
    if scene_id not in SCENE_INDEX:
        raise KeyError(f"未知场景：{scene_id}（共 {len(SCENES)} 个，可用 scenes list 查看）")
    return SCENE_INDEX[scene_id]


def scene_entry(scene_id: str, min_chunk: list[int], max_chunk: list[int]) -> dict[str, Any]:
    """把场景库条目实例化为世界内的一个区域（带 Chunk 包围盒）。"""
    entry = copy.deepcopy(get_scene(scene_id))
    entry["bounds"] = {"min_chunk": list(min_chunk), "max_chunk": list(max_chunk)}
    return entry


def derive_physics(scene_id: str) -> dict[str, Any]:
    """由 UEF 推导物理模块取值——场景决定介质与重力，而不是反过来。"""
    scene = get_scene(scene_id)
    uef = scene["uef"]
    medium = uef["medium"]
    return {
        "gravity": uef["gravity"],
        "air_density": medium["density_kg_m3"],
        "speed_of_sound": max(medium["sound_speed_mps"], 0.001),
        "fluid": {"viscosity": medium["viscosity_pa_s"]},
        "thermal": {"ambient_temperature_c": uef["energy"]["ambient_temperature_c"]},
    }


def derive_sensory(scene_id: str) -> dict[str, Any]:
    scene = get_scene(scene_id)
    uef = scene["uef"]
    declared = scene.get("sensory") or {}
    enclosure = uef["terrain"]["enclosure"]
    if declared.get("reverb_preset"):
        reverb = declared["reverb_preset"]
    elif uef["medium"]["kind"] in ("water", "seawater"):
        reverb = "underwater"
    elif uef["medium"]["kind"] == "vacuum":
        reverb = "custom"
    elif enclosure >= 0.8:
        reverb = "cave"
    elif enclosure >= 0.4:
        reverb = "small_room"
    elif uef["resources"]["biomass"] >= 0.7:
        reverb = "forest"
    else:
        reverb = "outdoor_open"
    light_ev = declared.get("ambient_light_ev")
    if light_ev is None:
        light_ev = round(-6 + 8 * uef["energy"]["solar_irradiance_scale"], 1)
    return {
        "reverb_preset": reverb,
        "occlusion_attenuation": round(0.2 + 0.7 * enclosure, 2),
        "ambient_light_ev": light_ev,
        "scent_refs": declared.get("scent_refs", []),
    }


UNIFIED_MODEL: dict[str, Any] = {
    "name": "统一环境场 UEF",
    "summary": "所有场景共用六轴环境参数；新增场景只填坐标，物理/生态/五感按轴推导。",
    "axes": [
        {"key": "gravity", "name": "重力场", "unit": "m/s²",
         "drives": ["坠落伤害", "跳跃高度", "抛射轨迹", "结构承重"]},
        {"key": "medium", "name": "介质", "unit": "kg/m³ · kPa · m/s",
         "drives": ["声音传播", "浮力", "燃烧与氧化", "传播衰减", "五感混响"]},
        {"key": "energy", "name": "能量", "unit": "℃ · 无量纲",
         "drives": ["体温平衡", "光照曝光", "辐射损伤", "昼夜与季节"]},
        {"key": "terrain", "name": "地形", "unit": "m · ° · 无量纲",
         "drives": ["移动速度", "载具可达性", "视野遮挡", "建筑形态"]},
        {"key": "resources", "name": "资源", "unit": "无量纲",
         "drives": ["人口承载力", "物种分布", "经济产出", "生存压力"]},
        {"key": "society", "name": "社会", "unit": "人/km² · 无量纲",
         "drives": ["NPC 密度与层级", "犯罪与秩序", "建筑与道路密度"]},
    ],
    "rules": [
        {
            "id": "medium_law",
            "name": "介质定律",
            "statement": "介质决定传播：真空无声、无燃烧；水声速约为空气 4.3 倍、传播损耗更高；稀薄大气降低音速与燃烧速率。",
            "derives": ["physics.air_density", "physics.speed_of_sound", "physics.combustion", "audio.reverb_preset"],
        },
        {
            "id": "energy_gradient",
            "name": "能量梯度定律",
            "statement": "温度 = 辐照收入 - 温室保留 - 垂直递减（约 6.5℃/km）；深海与洞穴恒温，真空靠辐射散热。",
            "derives": ["physics.thermal", "player_body.vitals.temperature", "visual.exposure"],
        },
        {
            "id": "reachability",
            "name": "可达性定律",
            "statement": "坡度与起伏决定移动倍率与可用载具；封闭度决定照明与空气循环需求。",
            "derives": ["player_body.status_effects", "visual.brightness_adapt_speed", "audio.occlusion_attenuation"],
        },
        {
            "id": "carrying_capacity",
            "name": "承载力定律",
            "statement": "人口密度上限 ≈ f(水, 生物量, 可耕地, 建成度)；城市用建成度换取本地生物量，靠贸易维持。",
            "derives": ["ecology.ecosystem.carrying_capacity_scale", "npc_agent.layers", "npc_agent.economy"],
        },
        {
            "id": "hazard_conservation",
            "name": "生存压力守恒",
            "statement": "危害不是独立配置，而是六轴的派生量：缺氧←氧含量，失温←能量，溺水←介质，辐射←能量/大气。",
            "derives": ["player_body.injury", "player_body.status_effects"],
        },
    ],
}


def unified_model() -> dict[str, Any]:
    return copy.deepcopy(UNIFIED_MODEL)
