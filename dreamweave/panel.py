"""M005 编辑器面板反射：把 JSON Schema 转成字段/控件树，供可视化编辑器直接渲染。

编辑器不再维护第二套字段清单——面板的控件类型、取值范围、枚举、必填状态全部
来自校验用的同一份 Schema，避免「UI 能改但校验拒绝」的漂移。
"""

from __future__ import annotations

import copy
from typing import Any

from .schemas import MANIFEST_SCHEMA, MODULE_SCHEMAS, SchemaSet

MAX_DEPTH = 8


def _pointer(document: Any, pointer: str) -> Any:
    node = document
    for raw in pointer.split("/"):
        if not raw:
            continue
        token = raw.replace("~1", "/").replace("~0", "~")
        node = node[token]
    return node


def _lookup(schemas: SchemaSet, document: dict[str, Any], ref: str) -> tuple[Any, dict[str, Any]]:
    """返回 (目标节点, 该节点所在的文档)；跨文件引用后，后续本地引用以新文档为根。"""
    base, _, pointer = ref.partition("#")
    target_document = schemas.schemas[base] if base else document
    return _pointer(target_document, pointer), target_document


def _resolve(
    schemas: SchemaSet, document: dict[str, Any], node: Any, depth: int = 0
) -> tuple[dict[str, Any], dict[str, Any]]:
    """展开 $ref，返回 (节点, 节点所属文档)；同级的 description 等关键字覆盖被引用定义。"""
    if not isinstance(node, dict):
        return {}, document
    if "$ref" not in node or depth >= MAX_DEPTH:
        return node, document
    target, target_document = _lookup(schemas, document, node["$ref"])
    merged = copy.deepcopy(target)
    for key, value in node.items():
        if key != "$ref":
            merged[key] = value
    return _resolve(schemas, target_document, merged, depth + 1)


def _bounds(node: dict[str, Any]) -> tuple[float | None, float | None]:
    low = node.get("minimum")
    if low is None and isinstance(node.get("exclusiveMinimum"), (int, float)):
        low = node["exclusiveMinimum"]
    high = node.get("maximum")
    return low, high


def _widget(node: dict[str, Any]) -> str:
    if "const" in node:
        return "readonly"
    if "enum" in node:
        return "readonly" if len(node["enum"]) == 1 else "select"
    kind = node.get("type")
    if kind == "boolean":
        return "toggle"
    if kind == "integer":
        return "integer"
    if kind == "number":
        low, high = _bounds(node)
        return "slider" if low == 0 and high == 1 else "number"
    if kind == "array":
        return "list"
    if kind == "object" or "properties" in node:
        if "properties" not in node and isinstance(node.get("additionalProperties"), dict):
            return "map"
        return "group"
    return "text"


def _build_fields(
    node: dict[str, Any],
    document: dict[str, Any],
    schemas: SchemaSet,
    prefix: str = "",
    depth: int = 0,
) -> list[dict[str, Any]]:
    properties = node.get("properties") or {}
    required = set(node.get("required") or [])
    fields = []
    for name, raw in properties.items():
        resolved, resolved_doc = _resolve(schemas, document, raw)
        fields.append(_build_field(name, resolved, resolved_doc, name in required, schemas, prefix, depth))
    return fields


def _build_field(
    name: str,
    node: dict[str, Any],
    document: dict[str, Any],
    required: bool,
    schemas: SchemaSet,
    prefix: str,
    depth: int,
) -> dict[str, Any]:
    path = f"{prefix}/{name}"
    widget = _widget(node)
    record: dict[str, Any] = {
        "path": path,
        "key": name,
        "widget": widget,
        "type": node.get("type", "object" if "properties" in node else "any"),
        "required": required,
    }
    if "description" in node:
        record["description"] = node["description"]
    if "default" in node:
        record["default"] = node["default"]
    if "const" in node:
        record["value"] = node["const"]
    elif widget == "readonly" and node.get("enum"):
        record["value"] = node["enum"][0]
    if "enum" in node and widget == "select":
        record["options"] = list(node["enum"])

    if widget in ("number", "integer", "slider"):
        low, high = _bounds(node)
        if low is not None:
            record["minimum"] = low
        if high is not None:
            record["maximum"] = high
        record["step"] = 1 if widget == "integer" else (0.01 if widget == "slider" else None)
        if record["step"] is None:
            record.pop("step")

    if widget == "text" and "maxLength" in node:
        record["max_length"] = node["maxLength"]

    if widget == "group" and depth < MAX_DEPTH:
        record["fields"] = _build_fields(node, document, schemas, path, depth + 1)

    if widget == "map":
        record["key_pattern"] = (node.get("propertyNames") or {}).get("pattern")
        value, _ = _resolve(schemas, document, node.get("additionalProperties") or {})
        record["value_widget"] = _widget(value)
        if "enum" in value:
            record["value_options"] = list(value["enum"])
        if "description" in node:
            record["description"] = node["description"]

    if widget == "list":
        record["min_items"] = node.get("minItems", 0)
        if "maxItems" in node:
            record["max_items"] = node["maxItems"]
        if node.get("uniqueItems"):
            record["unique_items"] = True
        item, item_document = _resolve(schemas, document, node.get("items") or {})
        record["item"] = {"widget": _widget(item), "type": item.get("type", "any")}
        if "enum" in item:
            record["item"]["options"] = list(item["enum"])
        if item.get("properties") and depth < MAX_DEPTH:
            record["item_fields"] = _build_fields(item, item_document, schemas, f"{path}/[]", depth + 1)

    return record


def build_panel(module_key: str, schemas: SchemaSet | None = None) -> dict[str, Any]:
    """生成指定模块的编辑器面板树。module_key 取模块名或 manifest。"""
    schemas = schemas or SchemaSet()
    if module_key == "manifest":
        schema_file = MANIFEST_SCHEMA
    elif module_key in MODULE_SCHEMAS:
        schema_file = MODULE_SCHEMAS[module_key]
    else:
        raise KeyError(f"未知模块：{module_key}（可选：manifest, {', '.join(MODULE_SCHEMAS)}）")

    node = schemas.schemas[f"https://schemas.dreamweave.local/{schema_file}"]
    return {
        "module": module_key,
        "schema": schema_file,
        "title": node.get("title", module_key),
        "description": node.get("description", ""),
        "path": "/",
        "fields": _build_fields(node, node, schemas),
    }
