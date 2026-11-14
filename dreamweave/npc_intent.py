"""M006-04 闭环：校验 LLM NPC 回复，并把 world_edit 意图转成受控配置补丁。

模型只能输出 npc_intent.schema.json 约定的结构；其中修改世界的部分必须写成受控句式，
再走 M006-03 翻译与全量校验。权限上仅 llm_core 层 NPC 可触发世界修改。
"""

from __future__ import annotations

from typing import Any

from .schemas import SchemaSet
from .translator import translate
from .world import WorldSession

REPLY_SCHEMA = "npc_intent.schema.json"
WORLD_EDIT_LAYERS = {"llm_core"}
PASSIVE_ACTIONS = {"none", "move", "trade"}


def handle_npc_reply(
    session: WorldSession,
    npc_id: str,
    payload: dict[str, Any],
    dry_run: bool = True,
    schema_set: SchemaSet | None = None,
) -> dict[str, Any]:
    """返回统一结果结构，供引擎/编辑器直接消费。"""
    result: dict[str, Any] = {
        "accepted": False,
        "npc_id": npc_id,
        "dialogue": payload.get("dialogue"),
        "action_type": (payload.get("action") or {}).get("type"),
        "message": "",
        "operations": [],
        "errors": [],
        "warnings": [],
    }

    schemas = schema_set or SchemaSet()
    issues = schemas.iter_errors(payload, REPLY_SCHEMA)
    if issues:
        result["message"] = "NPC 回复不符合 npc_intent 契约"
        result["errors"] = [{"code": "NPC_REPLY_SCHEMA", "path": pointer, "message": message} for pointer, message in issues]
        return result

    if payload.get("npc_id") != npc_id:
        result["message"] = "回复中的 npc_id 与请求不一致"
        result["errors"] = [{"code": "NPC_REPLY_ID_MISMATCH", "path": "npc_id", "message": f"{payload.get('npc_id')} != {npc_id}"}]
        return result

    roster = session.modules.get("npc_agent", {}).get("npc_roster", [])
    npc = next((item for item in roster if item.get("id") == npc_id), None)
    if npc is None:
        result["message"] = f"世界中不存在 NPC：{npc_id}"
        result["errors"] = [{"code": "NPC_NOT_FOUND", "path": "npc_id", "message": npc_id}]
        return result

    action = payload.get("action") or {}
    action_type = action.get("type")

    if action_type in PASSIVE_ACTIONS:
        result["accepted"] = True
        result["message"] = "仅对话，不修改世界"
        return result

    if npc.get("layer") not in WORLD_EDIT_LAYERS:
        result["message"] = "该 NPC 所在层无世界修改权限"
        result["errors"] = [
            {
                "code": "NPC_NOT_AUTHORIZED",
                "path": "action/type",
                "message": f"{npc.get('layer')} 层 NPC 不能执行 world_edit",
            }
        ]
        return result

    translation = translate(session, action.get("instruction", ""), dry_run=dry_run)
    result.update(
        accepted=translation["accepted"],
        message=translation["message"],
        operations=translation["operations"],
        errors=translation["errors"],
        warnings=translation["warnings"],
    )
    return result
