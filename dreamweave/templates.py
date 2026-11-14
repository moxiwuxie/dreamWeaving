"""M001-03 世界模板：从 blank / earth / fantasy 一键生成可校验的世界包。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .packaging import LOADER_VERSION
from .scenes import scene_entry
from .sensory_bus import default_bus_config
from .validator import validate_world

TEMPLATES = ("blank", "earth", "fantasy")
WORLD_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

MODULE_SENSORY = ("visual", "audio", "haptic", "scent", "taste")


def _physics(template: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "0.1.0",
        "preset": "earth",
        "gravity": -9.81,
        "air_density": 1.225,
        "speed_of_sound": 343.0,
        "friction": {"static": 0.8, "dynamic": 0.6, "rolling": 0.02},
        "fluid": {"viscosity": 0.001, "surface_tension": 0.072, "turbulence_scale": 1.0},
        "thermal": {
            "ambient_temperature_c": 15,
            "air_heat_capacity": 1005,
            "conductivity_scale": 1.0,
            "radiation_scale": 1.0,
        },
        "combustion": {
            "ignition_temperature_c": 300,
            "burn_rate": 0.01,
            "oxygen_consumption": 3.5,
            "smoke_yield": 0.05,
        },
        "rigid_body": {"substeps": 4, "sleep_threshold": 0.05, "max_contact_pairs": 100000},
        "coupling_tick_order": ["thermal", "fluid", "combustion", "deformation", "rigid_body"],
        "deformation": {"youngs_modulus_gpa": 20, "poisson_ratio": 0.3, "fracture_stress_mpa": 60},
    }
    if template == "blank":
        data["preset"] = "custom"
        data.pop("deformation")
    elif template == "fantasy":
        data.update({"preset": "fantasy", "gravity": -7.2, "air_density": 1.6, "speed_of_sound": 300.0})
        data["thermal"]["ambient_temperature_c"] = 12
        data["fluid"].update({"viscosity": 0.002, "surface_tension": 0.09})
        data["combustion"]["ignition_temperature_c"] = 260
        data["deformation"] = {"youngs_modulus_gpa": 12, "poisson_ratio": 0.28, "fracture_stress_mpa": 40}
    return data


def _ecology(template: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "0.1.0",
        "geology": {
            "tectonics_enabled": True,
            "plate_motion_rate": 2.5,
            "uplift_rate": 8,
            "erosion": {"water": 1.0, "wind": 0.4, "freeze_thaw": 0.3},
        },
        "climate": {
            "base_temperature_c": 15,
            "greenhouse_factor": 1.0,
            "precipitation_scale": 1.0,
            "seasonal_amplitude_c": 12,
            "prevailing_wind": {"direction_deg": 270, "speed_mps": 6},
            "weather_events": [],
        },
        "ecosystem": {
            "carrying_capacity_scale": 1.0,
            "migration_enabled": True,
            "mutation_rate": 0.001,
            "biomes": [],
        },
        "species": [],
    }
    if template == "blank":
        data["geology"]["tectonics_enabled"] = False
        data["ecosystem"]["biomes"] = [{"id": "plains", "temperature_range_c": [-10, 35], "humidity_range": [0.2, 0.7]}]
        return data

    if template == "earth":
        data["ecosystem"]["biomes"] = [
            {"id": "temperate_forest", "temperature_range_c": [-5, 28], "humidity_range": [0.5, 0.9]},
            {"id": "grassland", "temperature_range_c": [-10, 35], "humidity_range": [0.25, 0.6]},
        ]
        data["climate"]["weather_events"] = [
            {
                "id": "spring_rain",
                "kind": "rain",
                "probability_per_day": 0.25,
                "duration_min": 180,
                "intensity": 0.5,
                "trigger_conditions": ["humidity > 0.6", "temperature_c > 5"],
            },
            {
                "id": "winter_snow",
                "kind": "snow",
                "probability_per_day": 0.3,
                "duration_min": 240,
                "intensity": 0.6,
                "trigger_conditions": ["temperature_c < 0"],
            },
        ]
        data["species"] = [
            {
                "id": "grass",
                "name": "野草",
                "kingdom": "plant",
                "average_mass_kg": 0.2,
                "lifespan_days": 200,
                "maturity_days": 20,
                "reproduction_rate": 500,
                "habitats": ["grassland", "temperate_forest"],
                "food_web": {"prey": [], "predators": ["rabbit", "deer"]},
            },
            {
                "id": "oak",
                "name": "橡树",
                "kingdom": "plant",
                "average_mass_kg": 1200,
                "lifespan_days": 18000,
                "maturity_days": 1825,
                "reproduction_rate": 20,
                "habitats": ["temperate_forest"],
                "food_web": {"prey": [], "predators": ["deer"]},
            },
            {
                "id": "rabbit",
                "name": "野兔",
                "kingdom": "herbivore",
                "average_mass_kg": 2,
                "lifespan_days": 1500,
                "maturity_days": 180,
                "reproduction_rate": 12,
                "habitats": ["grassland"],
                "food_web": {"prey": ["grass"], "predators": ["wolf"]},
            },
            {
                "id": "deer",
                "name": "赤鹿",
                "kingdom": "herbivore",
                "average_mass_kg": 120,
                "lifespan_days": 5000,
                "maturity_days": 730,
                "reproduction_rate": 1,
                "habitats": ["temperate_forest"],
                "food_web": {"prey": ["grass", "oak"], "predators": ["wolf"]},
            },
            {
                "id": "wolf",
                "name": "灰狼",
                "kingdom": "carnivore",
                "average_mass_kg": 40,
                "lifespan_days": 4000,
                "maturity_days": 730,
                "reproduction_rate": 2,
                "habitats": ["temperate_forest", "grassland"],
                "food_web": {"prey": ["rabbit", "deer"], "predators": []},
            },
        ]
        return data

    data["climate"].update({"base_temperature_c": 9, "greenhouse_factor": 0.8, "seasonal_amplitude_c": 18})
    data["ecosystem"]["biomes"] = [
        {"id": "crystal_steppe", "temperature_range_c": [-20, 20], "humidity_range": [0.05, 0.4]},
        {"id": "moonlit_grove", "temperature_range_c": [0, 26], "humidity_range": [0.4, 0.9]},
    ]
    data["climate"]["weather_events"] = [
        {
            "id": "aurora_tide",
            "kind": "aurora",
            "probability_per_day": 0.12,
            "duration_min": 300,
            "intensity": 0.7,
            "trigger_conditions": ["night", "humidity < 0.5"],
        },
        {
            "id": "ash_fog",
            "kind": "fog",
            "probability_per_day": 0.2,
            "duration_min": 120,
            "intensity": 0.5,
            "trigger_conditions": ["wind_speed_mps < 3"],
        },
    ]
    data["species"] = [
        {
            "id": "glow_moss",
            "name": "幽光苔",
            "kingdom": "plant",
            "average_mass_kg": 0.05,
            "lifespan_days": 900,
            "maturity_days": 10,
            "reproduction_rate": 300,
            "habitats": ["moonlit_grove"],
            "food_web": {"prey": [], "predators": ["ember_hare"]},
        },
        {
            "id": "ember_hare",
            "name": "烬兔",
            "kingdom": "herbivore",
            "average_mass_kg": 3,
            "lifespan_days": 1200,
            "maturity_days": 150,
            "reproduction_rate": 9,
            "habitats": ["crystal_steppe", "moonlit_grove"],
            "food_web": {"prey": ["glow_moss"], "predators": ["dream_wolf"]},
        },
        {
            "id": "dream_wolf",
            "name": "梦魇狼",
            "kingdom": "carnivore",
            "average_mass_kg": 55,
            "lifespan_days": 3600,
            "maturity_days": 600,
            "reproduction_rate": 2,
            "habitats": ["crystal_steppe"],
            "food_web": {"prey": ["ember_hare"], "predators": []},
        },
        {
            "id": "moon_moth",
            "name": "月蛾",
            "kingdom": "fantasy",
            "average_mass_kg": 0.4,
            "lifespan_days": 240,
            "maturity_days": 30,
            "reproduction_rate": 40,
            "habitats": ["moonlit_grove"],
            "food_web": {"prey": [], "predators": []},
        },
    ]
    return data


def _player(template: str) -> dict[str, Any]:
    effects = [
        {
            "id": "hungry",
            "trigger": "hunger_low",
            "threshold": 0.3,
            "feedback": {"vfx": "vfx_desaturate", "sfx": "sfx_stomach_growl", "scent": "scent_bread"},
            "modifiers": {"stamina_multiplier": 0.7},
        },
        {
            "id": "exhausted",
            "trigger": "fatigue_high",
            "threshold": 0.8,
            "feedback": {"vfx": "vfx_eye_droop", "sfx": "sfx_slow_breath", "haptic": "haptic_slow_pulse"},
            "modifiers": {"speed_multiplier": 0.65, "vision_blur": 0.3},
        },
        {
            "id": "bleeding",
            "trigger": "bleeding",
            "threshold": 0.1,
            "feedback": {"vfx": "vfx_blood_drip", "sfx": "sfx_heartbeat", "scent": "scent_iron"},
            "modifiers": {"stamina_multiplier": 0.8},
        },
    ]
    if template == "blank":
        effects = effects[:1]
    return {
        "schema_version": "0.1.0",
        "vitals": {
            "temperature": {
                "baseline_c": 36.8,
                "comfort_ambient_range_c": [10, 30],
                "critical_low_c": 28,
                "critical_high_c": 42,
            },
            "hunger": {"drain_per_hour": 0.04, "starvation_threshold": 0.1, "damage_per_hour": 20},
            "thirst": {"drain_per_hour": 0.06, "dehydration_threshold": 0.1, "damage_per_hour": 30},
            "fatigue": {"drain_per_hour_awake": 0.05, "recovery_per_hour_asleep": 0.12, "collapse_threshold": 0.95},
            "oxygen": {"capacity_seconds": 90, "regen_per_second": 10, "drowning_damage_per_second": 8},
        },
        "injury": {
            "bleed_rate_per_min": 0.5,
            "heal_rate_per_day": 0.15,
            "fracture_energy_joule": 80,
            "pain_curve": [
                {"input": 0, "output": 0},
                {"input": 0.3, "output": 0.2},
                {"input": 0.6, "output": 0.6},
                {"input": 1, "output": 1},
            ],
        },
        "immunity": {"base_resistance": 0.6, "infection_chance_per_contact": 0.08, "recovery_rate_per_hour": 0.05},
        "survival": {
            "auto_eat": True,
            "auto_drink": True,
            "eat_threshold": 0.35,
            "drink_threshold": 0.4,
            "eat_rate_per_hour": 0.3,
            "drink_rate_per_hour": 0.6,
        },
        "status_effects": effects,
    }


def _schedule_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": "settler_day",
            "entries": [
                {"start_minute": 0, "end_minute": 420, "activity": "sleep", "location": "home"},
                {"start_minute": 420, "end_minute": 540, "activity": "eat", "location": "hearth"},
                {"start_minute": 540, "end_minute": 1080, "activity": "work", "location": "field"},
                {"start_minute": 1080, "end_minute": 1320, "activity": "socialize", "location": "square"},
                {"start_minute": 1320, "end_minute": 1440, "activity": "sleep", "location": "home"},
            ],
        }
    ]


def _npc(template: str) -> dict[str, Any]:
    goods = {
        "blank": [{"id": "wheat", "name": "小麦", "base_price": 3, "unit": "份", "supply_elasticity": 0.8, "demand_elasticity": -0.6}],
        "earth": [
            {"id": "wheat", "name": "小麦", "base_price": 3, "unit": "份", "supply_elasticity": 0.8, "demand_elasticity": -0.6},
            {"id": "bread", "name": "面包", "base_price": 6, "unit": "个", "supply_elasticity": 0.5, "demand_elasticity": -0.9},
            {"id": "water", "name": "清水", "base_price": 1, "unit": "壶", "supply_elasticity": 1.2, "demand_elasticity": -0.4},
            {"id": "timber", "name": "木材", "base_price": 8, "unit": "捆", "supply_elasticity": 0.7, "demand_elasticity": -0.5},
            {"id": "iron_ore", "name": "铁矿石", "base_price": 14, "unit": "块", "supply_elasticity": 0.3, "demand_elasticity": -0.7},
            {"id": "medicine", "name": "草药", "base_price": 22, "unit": "包", "supply_elasticity": 0.4, "demand_elasticity": -1.1},
        ],
        "fantasy": [
            {"id": "moon_sugar", "name": "月糖", "base_price": 9, "unit": "块", "supply_elasticity": 0.6, "demand_elasticity": -0.8},
            {"id": "crystal_shard", "name": "晶簇", "base_price": 26, "unit": "枚", "supply_elasticity": 0.2, "demand_elasticity": -1.0},
            {"id": "dream_herb", "name": "梦草", "base_price": 15, "unit": "束", "supply_elasticity": 0.5, "demand_elasticity": -0.9},
            {"id": "ember_meat", "name": "烬兔肉", "base_price": 12, "unit": "份", "supply_elasticity": 0.7, "demand_elasticity": -0.6},
        ],
    }[template]

    roster: list[dict[str, Any]] = []
    if template != "blank":
        names = {
            "earth": [("qiu_farmer", "邱农", "踏实，日出而作。"), ("guo_guard", "郭卫", "寡言，夜里巡街。")],
            "fantasy": [("lin_dreamer", "林梦", "收集别人的梦。"), ("yan_watcher", "焰望", "守着灯塔与极光。")],
        }[template]
        roster = [
            {
                "id": names[0][0],
                "name": names[0][1],
                "layer": "mass_life",
                "schedule_template": "settler_day",
                "persona": {
                    "traits": {"openness": 0.5, "conscientiousness": 0.8, "extraversion": 0.4, "agreeableness": 0.7, "neuroticism": 0.3},
                    "goals": ["把这一季的收成存够", "和邻居保持来往"],
                    "speech_style": names[0][2],
                },
                "memory": {"capacity": 256, "seed_events": [{"day": 0, "summary": "在世界初始之地醒来。", "salience": 0.6}]},
                "relations": [{"target": names[1][0], "trust": 0.6, "familiarity": 0.5}],
            },
            {
                "id": names[1][0],
                "name": names[1][1],
                "layer": "mass_life",
                "schedule_template": "settler_day",
                "persona": {
                    "traits": {"openness": 0.3, "conscientiousness": 0.9, "extraversion": 0.2, "agreeableness": 0.5, "neuroticism": 0.4},
                    "goals": ["守住夜间秩序"],
                    "speech_style": names[1][2],
                },
                "memory": {"capacity": 256, "seed_events": [{"day": 0, "summary": "接过了巡夜的职责。", "salience": 0.5}]},
                "relations": [{"target": names[0][0], "trust": 0.6, "familiarity": 0.5}],
            },
        ]

    if roster:
        scene_ids = {"earth": ("farmland", "city_core"), "fantasy": ("forest", "hills")}[template]
        for agent, scene_id in zip(roster, scene_ids):
            agent["scene_id"] = scene_id

    location_scene_map = {
        "earth": {"hearth": "city_core", "field": "farmland", "square": "city_core"},
        "fantasy": {"hearth": "hills", "field": "forest", "square": "hills"},
    }.get(template, {})

    return {
        "schema_version": "0.1.0",
        "layers": {
            "crowd": {"enabled": True, "max_agents": 2000, "update_hz": 10, "lod_radius_m": 120},
            "mass_life": {"enabled": True, "max_agents": 400, "update_hz": 5, "lod_radius_m": 800, "offline_tick_minutes": 30},
            "llm_core": {
                "enabled": False,
                "max_active_agents": 0,
                "memory_window_tokens": 4096,
                "model_ref": "npc_default",
            },
        },
        "economy": {"currency": "coin", "inflation_target": 0.02, "goods": goods},
        "laws": [
            {"id": "no_theft", "description": "不得窃取他人财物。", "severity": "minor", "applies_to": ["crowd", "mass_life", "llm_core", "player"]},
            {"id": "no_murder", "description": "不得杀害他人。", "severity": "capital", "applies_to": ["mass_life", "llm_core", "player"]},
        ],
        "schedule_templates": _schedule_templates(),
        "location_scene_map": location_scene_map,
        "npc_roster": roster,
    }


def _sensory(template: str) -> dict[str, dict[str, Any]]:
    atmosphere = (
        {"rayleigh": 1.0, "mie": 0.7, "turbidity": 0.5}
        if template == "earth"
        else {"rayleigh": 0.6, "mie": 0.9, "turbidity": 0.8}
    )
    scene_reverb = {
        "earth": {"farmland": "outdoor_open"},
        "fantasy": {"hills": "outdoor_open", "floating_island": "cathedral"},
    }.get(template, {})
    return {
        "visual": {
            "schema_version": "0.1.0",
            "renderer": {
                "backend": "ue5",
                "global_illumination": "lumen",
                "geometry": "nanite",
                "anti_aliasing": "tsr",
                "upscaler": "dlss",
                "shadow": "virtual_shadow_maps",
            },
            "perspective": {
                "default": "first_person",
                "blend_seconds": 0.25,
                "first_person": {
                    "eye_height_m": 1.65,
                    "forward_offset_m": 0.1,
                    "fov_deg": 90,
                    "near_clip_m": 0.05,
                    "far_clip_m": 20000,
                    "pitch_min_deg": -85,
                    "pitch_max_deg": 85,
                    "head_bob": True,
                },
                "third_person": {
                    "boom_length_m": 3.2,
                    "shoulder_offset_m": 0.6,
                    "height_offset_m": 1.7,
                    "fov_deg": 75,
                    "near_clip_m": 0.1,
                    "far_clip_m": 20000,
                    "collision_probe_radius_m": 0.2,
                    "min_boom_m": 0.5,
                    "pitch_min_deg": -70,
                    "pitch_max_deg": 45,
                },
            },
            "brightness_adapt_speed": 1.0,
            "atmosphere": atmosphere,
            "exposure": {
                "min_ev": -4,
                "max_ev": 6,
                "adapt_curve": [{"input": 0, "output": -3}, {"input": 0.5, "output": 0}, {"input": 1, "output": 4}],
            },
            "depth_of_field": {"enabled": True, "aperture_fstop": 2.8, "focus_distance_m": 12},
            "motion_blur": {"enabled": True, "shutter_fraction": 0.5},
            "status_vfx_map": {
                "hungry": "vfx_desaturate",
                "exhausted": "vfx_eye_droop",
                "bleeding": "vfx_blood_drip",
            },
        },
        "audio": {
            "schema_version": "0.1.0",
            "backend": {
                "spatializer": "steam_audio",
                "mixer": "metasounds",
                "sample_rate_hz": 48000,
                "ambisonics": "third_order",
                "hrtf_profile": "steam_audio_default",
                "max_simultaneous_sources": 64,
            },
            "listener": {"follow": "camera", "update_rate_hz": 60},
            "scene_reverb_map": scene_reverb,
            "hrtf_enabled": True,
            "reverb_preset": "outdoor_open",
            "occlusion_attenuation": 0.8,
            "layer_gain": {"far": 0.4, "mid": 0.7, "near": 1.0},
            "material_sound_map": {
                "wood": {
                    "impact": "sfx_wood_impact",
                    "friction": "sfx_wood_friction",
                    "break": "sfx_wood_break",
                    "step": "sfx_wood_step",
                },
                "stone": {
                    "impact": "sfx_stone_impact",
                    "friction": "sfx_stone_friction",
                    "break": "sfx_stone_break",
                    "step": "sfx_stone_step",
                },
            },
        },
        "haptic": {
            "schema_version": "0.1.0",
            "devices": {
                "dualsense": {"enabled": True, "trigger_strength": 0.8},
                "screen_rumble": {"enabled": True, "intensity": 0.3},
                "bhaptics": {"enabled": False, "vest_nodes": 0},
                "owo_vest": {"enabled": False, "electrode_pairs": 0},
                "dexmo": {"enabled": False, "force_limit_n": 0},
            },
            "vibration_wave_library": [
                {"id": "hit_light", "waveform": "pulse", "frequency_hz": 120, "amplitude": 0.3, "duration_ms": 90},
                {"id": "hit_heavy", "waveform": "saw", "frequency_hz": 80, "amplitude": 0.8, "duration_ms": 220},
                {"id": "slow_pulse", "waveform": "sine", "frequency_hz": 4, "amplitude": 0.25, "duration_ms": 700},
            ],
            "trigger_resistance_curve": [
                {"input": 0, "output": 0},
                {"input": 0.5, "output": 0.3},
                {"input": 1, "output": 0.9},
            ],
            "event_wave_map": [
                {"event": "melee_hit_light", "wave_id": "hit_light", "gain": 0.6},
                {"event": "melee_hit_heavy", "wave_id": "hit_heavy", "gain": 1.0},
                {"event": "status_exhausted", "wave_id": "slow_pulse", "gain": 0.4},
            ],
        },
        "scent": {
            "schema_version": "0.1.0",
            "scent_lib": [
                {"id": "scent_pine", "name": "松脂", "category": "woody", "base_intensity": 0.5, "descriptors": ["树脂", "清冽"]},
                {"id": "scent_bread", "name": "烤面包", "category": "food", "base_intensity": 0.8, "descriptors": ["麦香"]},
                {"id": "scent_iron", "name": "血腥味", "category": "animal", "base_intensity": 0.9, "descriptors": ["铁锈"]},
            ],
            "diffusion": {"wind_influence": 0.6, "distance_falloff_exp": 2, "persistence_seconds": 45, "max_concentration": 0.85},
            "hardware": {"ovr_omara": {"enabled": False, "cartridge_slots": 0}},
            "fallback": {
                "mode": "color_filter",
                "vfx": "vfx_scent_drift",
                "sfx": "sfx_scent_hint",
                "ui_text": "空气中有一丝淡淡的%s气味",
            },
        },
        "taste": {
            "schema_version": "0.1.0",
            "taste_basic_map": {"sweet": 1, "sour": 1, "salty": 1, "bitter": 1, "umami": 1},
            "food_bindings": [
                {
                    "food_id": "bread",
                    "taste": {"sweet": 0.3, "sour": 0.05, "salty": 0.2, "bitter": 0.05, "umami": 0.15},
                    "texture": "crunchy",
                    "serving_temperature": "warm",
                }
            ],
            "hardware": {"vocktail": {"enabled": False, "microcurrent_range_ua": 0}},
            "fallback": {
                "mode": "expression",
                "expression": "expr_savor",
                "sfx": "sfx_chew_loop",
                "ui_text": "口感%s，味道%s",
            },
        },
    }


def _sensory_bus(template: str) -> dict[str, Any]:
    """统一感官事件总线：与模板中的感官硬件开关保持一致。"""
    config = default_bus_config()
    config["channels"]["haptic"]["devices"].append(
        {"id": "screen_rumble", "enabled": True, "latency_ms": 16, "capabilities": ["screen_haptic"]}
    )
    if template == "fantasy":
        config["sync_window_ms"] = 50
        config["soft_sync_window_ms"] = 420
    return config


def _scenes(template: str) -> dict[str, Any] | None:
    """模板世界内预置的空间分区：同一套 UEF 坐标，只换包围盒。"""
    layouts: dict[str, list[tuple[str, tuple[int, int, int], tuple[int, int, int], str | None]]] = {
        "earth": [
            ("city_core", (0, 0, 0), (63, 31, 63), "grassland"),
            ("farmland", (64, 0, 0), (191, 31, 95), "grassland"),
            ("forest", (192, 0, 0), (319, 63, 127), "temperate_forest"),
            ("mountain_range", (320, 0, 0), (447, 63, 127), "temperate_forest"),
            ("coast", (448, 0, 0), (511, 31, 63), "grassland"),
        ],
        "fantasy": [
            ("forest", (0, 0, 0), (127, 63, 127), "moonlit_grove"),
            ("hills", (128, 0, 0), (255, 63, 127), "crystal_steppe"),
            ("floating_island", (256, 0, 0), (383, 95, 191), None),
        ],
    }
    if template not in layouts:
        return None
    scenes = []
    gravity = _physics(template)["gravity"]
    for scene_id, low, high, biome in layouts[template]:
        entry = scene_entry(scene_id, list(low), list(high))
        entry["uef"]["gravity"] = gravity
        if biome:
            entry["biome_ref"] = biome
        scenes.append(entry)
    return {"schema_version": "0.1.0", "scenes": scenes}


def _manifest(world_id: str, world_name: str, template: str) -> dict[str, Any]:
    terrain = {
        "blank": [128, 64, 128],
        "earth": [512, 128, 512],
        "fantasy": [384, 160, 384],
    }[template]
    modules = {name: {"path": f"config/{name}.json"} for name in ("physics", "ecology", "npc_agent", "player_body")}
    if template != "blank":
        modules.update({name: {"path": f"config/{name}.json"} for name in MODULE_SENSORY})
        modules["sensory_bus"] = {"path": "config/sensory_bus.json"}
        modules["scenes"] = {"path": "config/scenes.json"}
        modules["narrative"] = {"path": "config/narrative.json"}
    return {
        "schema_version": "0.1.0",
        "world_id": world_id,
        "world_name": world_name,
        "world_version": "0.1.0",
        "template": template,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "author": "dreamweave",
        "engine": {"min_loader_version": LOADER_VERSION, "target_build": "UE5.8-dev"},
        "modules": modules,
        "terrain": {"voxel_size_m": 0.1, "chunk_dim": [32, 32, 32], "world_bounds_chunks": terrain},
        "assets": [],
        "requires": {
            "optional_hardware": [] if template == "blank" else ["dualsense"],
            "feature_flags": [] if template == "blank" else ["weather_cycle", "sensory_fallback"],
        },
    }


def _narrative(template: str) -> dict[str, Any]:
    """剧情内核：国家/阶层/个体三层结构 + 现实因果律 + AI 生成契约。"""
    rules = [
        {
            "id": "scarcity_drives_conflict",
            "principle": "资源稀缺会放大群体冲突，并优先沿着既有的边界（国界、阶层、亲缘）爆发。",
            "observable": "水、粮、草场出现缺口时，越界、偷盗与械斗先在小范围增多。",
            "consequence": "若长期无视稀缺，冲突会从个体纠纷升级为群体对抗。",
            "weight": 0.95,
        },
        {
            "id": "power_corrupts",
            "principle": "权力缺少制衡时会转向寻租，掌握分配权的人最先获利。",
            "observable": "关卡、税吏、巡逻队出现差别执法与灰色收费。",
            "consequence": "制度信任流失，底层转向私下交易或暴力。",
            "weight": 0.9,
        },
        {
            "id": "information_asymmetry",
            "principle": "信息差决定议价权，先知道消息的人先行动。",
            "observable": "商队、信使、祭司比普通人更早调整价格与立场。",
            "consequence": "信息垄断者积累超额利益，其他人被结构性剥夺。",
            "weight": 0.85,
        },
        {
            "id": "grievance_accumulates",
            "principle": "未被回应的不满不会消失，只会累积并寻找出口。",
            "observable": "怨言先出现在酒馆与家庭，再出现在联名与逃役。",
            "consequence": "临界点后以暴动、迁徙或信仰狂热的形式释放。",
            "weight": 0.9,
        },
        {
            "id": "legitimacy_tradeoff",
            "principle": "秩序与自由此消彼长，任何稳定都要付出可见的代价。",
            "observable": "戒严换来安全，同时带来盘查、征用与告密。",
            "consequence": "过度追求秩序会制造新的反抗理由。",
            "weight": 0.8,
        },
        {
            "id": "kinship_first",
            "principle": "亲缘与地缘信任优先于制度信任，制度只在熟人网络失效处生效。",
            "observable": "纠纷先找族老而非官府，借贷先看血缘而非契约。",
            "consequence": "制度被架空，改革必须借道熟人网络才能落地。",
            "weight": 0.75,
        },
    ]
    generation = {
        "pov": "third_person_limited",
        "max_new_arcs_per_day": 2,
        "language": "zh-CN",
        "tone": "冷峻写实，克制的抒情",
        "must_respect": [rule["id"] for rule in rules],
        "forbidden": ["无动机的巧合", "全知视角直接揭示秘密", "一夜之间改变国家制度", "没有代价的胜利"],
        "uniqueness": "per_day",
        "novelty_window_days": 30,
    }

    if template == "earth":
        nations = [
            {
                "id": "river_kingdom",
                "name": "河谷王国",
                "kind": "nation",
                "government": "monarchy",
                "ideology": "以水利与谷仓维持王权合法性",
                "population": 420000,
                "stability": 0.72,
                "military": 0.6,
                "economy": 0.7,
                "territory_scene_refs": ["city_core", "farmland", "coast"],
                "foreign_policy": "mercantile",
            },
            {
                "id": "hill_clans",
                "name": "山岭部落",
                "kind": "tribe",
                "government": "council",
                "ideology": "草场共有，长老议事，拒绝常备官僚",
                "population": 96000,
                "stability": 0.55,
                "military": 0.45,
                "economy": 0.35,
                "territory_scene_refs": ["mountain_range", "forest"],
                "foreign_policy": "defensive",
            },
        ]
        classes = [
            {"id": "nobility", "name": "贵族", "nation_id": "river_kingdom", "share": 0.05, "income_index": 7.5, "mobility": 0.15, "obligations": ["提供军役", "主持祭祀"], "grievances": []},
            {"id": "merchants", "name": "商人", "nation_id": "river_kingdom", "share": 0.15, "income_index": 7.0, "mobility": 0.6, "obligations": ["缴纳商税"], "grievances": ["关卡林立"]},
            {"id": "farmers", "name": "农户", "nation_id": "river_kingdom", "share": 0.8, "income_index": 2.0, "mobility": 0.25, "obligations": ["纳粮", "服徭役"], "grievances": ["水渠失修", "谷贱伤农"]},
            {"id": "clan_warriors", "name": "部族武士", "nation_id": "hill_clans", "share": 0.2, "income_index": 5.5, "mobility": 0.35, "obligations": ["守界"], "grievances": ["草场缩小"]},
            {"id": "herders", "name": "牧民", "nation_id": "hill_clans", "share": 0.7, "income_index": 2.2, "mobility": 0.4, "obligations": ["贡牲畜"], "grievances": ["雪灾无补偿", "商队压价"]},
            {"id": "elders", "name": "长老", "nation_id": "hill_clans", "share": 0.1, "income_index": 6.0, "mobility": 0.2, "obligations": ["裁决纠纷"], "grievances": []},
        ]
        tensions = [
            {
                "id": "water_dispute",
                "name": "河源争水",
                "parties": [{"kind": "nation", "ref": "river_kingdom"}, {"kind": "nation", "ref": "hill_clans"}],
                "stake": "water",
                "stage": "escalating",
                "intensity": 0.65,
                "triggers": ["上游筑坝", "牲畜越界饮水"],
            },
            {
                "id": "grain_tax",
                "name": "谷税之争",
                "parties": [{"kind": "class", "ref": "farmers"}, {"kind": "class", "ref": "nobility"}],
                "stake": "food",
                "stage": "emerging",
                "intensity": 0.4,
                "triggers": ["加征军粮", "灾年不减税"],
            },
        ]
        arcs = [
            {
                "id": "qiu_seed",
                "title": "旱季里的种子",
                "npc_ref": "qiu_farmer",
                "nation_ref": "river_kingdom",
                "class_ref": "farmers",
                "desire": "保住祖田与来年的种子",
                "obstacle": "水渠被上游截流，税粮又不能少",
                "flaw": "固执，不肯求人",
                "secret": "偷偷藏起一袋良种",
                "stakes": "family",
                "stage": "setup",
                "tags": ["农业", "生存"],
            },
            {
                "id": "guo_ledger",
                "title": "夜巡人的账本",
                "npc_ref": "guo_guard",
                "nation_ref": "river_kingdom",
                "class_ref": "merchants",
                "desire": "查清商队走私",
                "obstacle": "上级与商队有牵连",
                "flaw": "多疑",
                "secret": "曾收过一袋盐的贿赂",
                "stakes": "honor",
                "stage": "rising",
                "tags": ["秩序", "腐败"],
            },
        ]
        chronicle = [
            {"id": "founding_stone", "day": 0, "summary": "河谷王国与山岭部落在河源立下界碑，约定共用水源。", "participants": ["river_kingdom", "hill_clans"], "tension_ref": "water_dispute"},
            {"id": "first_grain_tax", "day": 1, "summary": "王国以军粮为名加征一成谷税，农户在村口聚集。", "participants": ["farmers", "nobility"], "tension_ref": "grain_tax"},
        ]
    else:
        nations = [
            {
                "id": "moon_court",
                "name": "月庭",
                "kind": "city_state",
                "government": "council",
                "ideology": "以织梦术垄断解释权，用梦税供养塔城",
                "population": 58000,
                "stability": 0.68,
                "military": 0.3,
                "economy": 0.62,
                "territory_scene_refs": ["forest", "floating_island"],
                "foreign_policy": "isolationist",
            },
            {
                "id": "ash_tribe",
                "name": "烬部",
                "kind": "tribe",
                "government": "council",
                "ideology": "逐极光而居，以锻造换取通行",
                "population": 21000,
                "stability": 0.5,
                "military": 0.55,
                "economy": 0.4,
                "territory_scene_refs": ["hills"],
                "foreign_policy": "defensive",
            },
        ]
        classes = [
            {"id": "dream_weavers", "name": "织梦师", "nation_id": "moon_court", "share": 0.2, "income_index": 8.0, "mobility": 0.5, "obligations": ["维持梦境秩序"], "grievances": []},
            {"id": "candle_keepers", "name": "守灯人", "nation_id": "moon_court", "share": 0.8, "income_index": 2.8, "mobility": 0.3, "obligations": ["缴纳梦税", "轮值灯塔"], "grievances": ["梦税过重", "梦境被上层优先征用"]},
            {"id": "ash_nomads", "name": "逐光牧民", "nation_id": "ash_tribe", "share": 0.7, "income_index": 2.4, "mobility": 0.45, "obligations": ["迁徙时护火"], "grievances": ["极光季缩短"]},
            {"id": "ember_smiths", "name": "烬匠", "nation_id": "ash_tribe", "share": 0.3, "income_index": 6.2, "mobility": 0.35, "obligations": ["为部族锻造"], "grievances": ["矿石被月庭压价"]},
        ]
        tensions = [
            {
                "id": "dream_tithe",
                "name": "梦税之议",
                "parties": [{"kind": "class", "ref": "candle_keepers"}, {"kind": "class", "ref": "dream_weavers"}],
                "stake": "faith",
                "stage": "open",
                "intensity": 0.55,
                "triggers": ["加征梦税", "守灯人梦境被征用"],
            },
            {
                "id": "ember_road",
                "name": "烬路通行",
                "parties": [{"kind": "nation", "ref": "moon_court"}, {"kind": "nation", "ref": "ash_tribe"}],
                "stake": "trade",
                "stage": "emerging",
                "intensity": 0.45,
                "triggers": ["关闭商道", "矿石压价"],
            },
        ]
        arcs = [
            {
                "id": "lin_dream",
                "title": "借来的梦",
                "npc_ref": "lin_dreamer",
                "nation_ref": "moon_court",
                "class_ref": "dream_weavers",
                "desire": "收集一个不属于任何人的梦",
                "obstacle": "所有梦都被登记为月庭财产",
                "flaw": "无法对他人说出真话",
                "secret": "自己的梦是空的",
                "stakes": "identity",
                "stage": "setup",
                "tags": ["梦境", "身份"],
            },
            {
                "id": "yan_lighthouse",
                "title": "极光熄灭的那一夜",
                "npc_ref": "yan_watcher",
                "nation_ref": "ash_tribe",
                "class_ref": "ember_smiths",
                "desire": "让灯塔再亮一季",
                "obstacle": "矿石被月庭压价，火种将尽",
                "flaw": "把承诺看得比命重",
                "secret": "灯塔的火焰来自祖辈的骨灰",
                "stakes": "survival",
                "stage": "rising",
                "tags": ["守望", "牺牲"],
            },
        ]
        chronicle = [
            {"id": "moon_pact", "day": 0, "summary": "月庭与烬部约定以矿石换通行，梦税不涉烬部。", "participants": ["moon_court", "ash_tribe"], "tension_ref": "ember_road"},
            {"id": "tithe_notice", "day": 1, "summary": "月庭张贴加征梦税的告示，守灯人夜议。", "participants": ["candle_keepers", "dream_weavers"], "tension_ref": "dream_tithe"},
        ]

    quests: list[dict[str, Any]] = []
    if template == "earth":
        quests = [
            {
                "id": "draw_river_water",
                "title": "为旱地取水",
                "description": "到城中组织一次取水，缓解河谷两岸的用水争执。",
                "scene_id": "city_core",
                "action": "work",
                "duration_min": 60,
                "tension_id": "water_dispute",
                "participants": ["river_kingdom", "hill_clans"],
            },
            {
                "id": "inspect_granary",
                "title": "查验粮仓",
                "description": "巡一遍城中粮仓，记录税收与存粮的缺口。",
                "scene_id": "city_core",
                "action": "patrol",
                "duration_min": 90,
                "tension_id": "grain_tax",
                "participants": ["farmers", "merchants"],
            },
        ]
    elif template == "fantasy":
        quests = [
            {
                "id": "guard_grove",
                "title": "巡守月林",
                "description": "沿月林边界巡守一夜，记录梦境被征用的痕迹。",
                "scene_id": "forest",
                "action": "patrol",
                "duration_min": 90,
                "tension_id": "dream_tithe",
                "participants": ["candle_keepers", "dream_weavers"],
            },
            {
                "id": "escort_ember",
                "title": "护送烬火",
                "description": "把一程火种送过山岭，试探月庭与烬部的通行边界。",
                "scene_id": "hills",
                "action": "work",
                "duration_min": 120,
                "tension_id": "ember_road",
                "participants": ["moon_court", "ash_tribe"],
            },
        ]

    return {
        "schema_version": "0.1.0",
        "causal_rules": rules,
        "nations": nations,
        "classes": classes,
        "tensions": tensions,
        "arcs": arcs,
        "chronicle": chronicle,
        "quests": quests,
        "generation": generation,
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_world(
    dest: str | Path,
    world_id: str,
    world_name: str | None = None,
    template: str = "earth",
    overwrite: bool = False,
) -> dict[str, Any]:
    """按模板生成一个通过校验的世界包；目录非空时默认拒绝。"""
    if template not in TEMPLATES:
        raise KeyError(f"未知模板：{template}（可选：{', '.join(TEMPLATES)}）")

    target = Path(dest).expanduser()
    name = world_name or world_id
    result: dict[str, Any] = {
        "ok": False,
        "path": str(target),
        "world_id": world_id,
        "world_name": name,
        "template": template,
        "modules": 0,
        "errors": [],
        "warnings": [],
    }

    if not WORLD_ID_PATTERN.match(world_id):
        result["errors"] = [{"code": "INVALID_WORLD_ID", "message": "world_id 需为小写蛇形，长度 2~64"}]
        return result
    if not 1 <= len(name) <= 64:
        result["errors"] = [{"code": "INVALID_WORLD_NAME", "message": "世界名长度需为 1~64 字符"}]
        return result
    if target.exists() and any(target.iterdir()) and not overwrite:
        result["errors"] = [{"code": "DEST_NOT_EMPTY", "message": f"目标目录非空：{target}（使用 --force 覆盖）"}]
        return result

    manifest = _manifest(world_id, name, template)
    modules = {
        "physics": _physics(template),
        "ecology": _ecology(template),
        "npc_agent": _npc(template),
        "player_body": _player(template),
    }
    if template != "blank":
        modules.update(_sensory(template))
        modules["sensory_bus"] = _sensory_bus(template)
        modules["scenes"] = _scenes(template)
        modules["narrative"] = _narrative(template)

    _write_json(target / "world.json", manifest)
    for module, data in modules.items():
        _write_json(target / "config" / f"{module}.json", data)

    report = validate_world(target)
    result.update(
        {
            "ok": report.ok,
            "modules": len(modules),
            "errors": [issue.to_dict() for issue in report.errors],
            "warnings": [issue.to_dict() for issue in report.warnings],
        }
    )
    return result
