"""《织梦》可执行配置层：强 Schema 校验、世界接口与 AI 翻译。"""

from .llm import LLMError, generate_story
from .narrative import (
    advance_narrative,
    build_story_context,
    ecology_deltas,
    environment_deltas,
    escalate_stage,
    handle_story_outline,
    narrative_pressures,
    outline_quality_errors,
)
from .npc_context import build_npc_context
from .npc_intent import handle_npc_reply
from .npc_schedule import build_schedule, schedule_at
from .packaging import check_compatibility, inspect_package, pack_world, unpack_world
from .panel import build_panel
from .perception import HARD_SENSES, INTERFACE_SENSES, PERSPECTIVES, build_perception
from .scenes import get_scene, list_scenes, scene_entry, unified_model
from .sensory_bus import SensoryBusError, SensoryEventBus, build_sensory_plan, status_effects_as_events
from .simulation import Simulation
from .templates import create_world
from .translator import translate
from .validator import Issue, Report, validate_world
from .world import ApplyResult, WorldError, WorldSession

__all__ = [
    "ApplyResult",
    "Issue",
    "LLMError",
    "Report",
    "SensoryBusError",
    "SensoryEventBus",
    "Simulation",
    "WorldError",
    "WorldSession",
    "HARD_SENSES",
    "INTERFACE_SENSES",
    "PERSPECTIVES",
    "advance_narrative",
    "build_npc_context",
    "build_panel",
    "build_perception",
    "build_schedule",
    "build_sensory_plan",
    "build_story_context",
    "check_compatibility",
    "create_world",
    "ecology_deltas",
    "environment_deltas",
    "escalate_stage",
    "generate_story",
    "get_scene",
    "handle_npc_reply",
    "handle_story_outline",
    "inspect_package",
    "list_scenes",
    "narrative_pressures",
    "outline_quality_errors",
    "pack_world",
    "scene_entry",
    "schedule_at",
    "status_effects_as_events",
    "translate",
    "unified_model",
    "unpack_world",
    "validate_world",
]
__version__ = "0.1.0"
