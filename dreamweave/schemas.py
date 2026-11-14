"""Schema 注册表：一次加载全部内核 Schema，供跨文件 $ref 解析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parent / "schema_data"

# 模块键 -> Schema 文件，对应 world_manifest.modules
MODULE_SCHEMAS = {
    "physics": "world_physics.schema.json",
    "ecology": "world_ecology.schema.json",
    "npc_agent": "npc_agent.schema.json",
    "player_body": "player_body.schema.json",
    "scenes": "world_scenes.schema.json",
    "narrative": "world_narrative.schema.json",
    "visual": "sensory_visual.schema.json",
    "audio": "sensory_audio.schema.json",
    "haptic": "sensory_haptic.schema.json",
    "scent": "sensory_scent.schema.json",
    "taste": "sensory_taste.schema.json",
    "sensory_bus": "sensory_bus.schema.json",
}

# 运行时输入 Schema，不对应 world_manifest 模块
RUNTIME_SCHEMAS = {
    "sensory_event": "sensory_event.schema.json",
}

# manifest.modules 中必须存在的模块
CORE_MODULES = ("physics", "ecology", "npc_agent", "player_body")

MANIFEST_SCHEMA = "world_manifest.schema.json"


def load_schemas(schema_dir: Path = SCHEMA_DIR) -> dict[str, Any]:
    """读取包内 schema_data/ 下全部 Schema，按 $id 建索引。"""
    if not schema_dir.is_dir():
        raise FileNotFoundError(f"找不到 Schema 目录：{schema_dir}（安装包缺少 package-data？）")
    schemas: dict[str, Any] = {}
    for path in sorted(schema_dir.glob("*.schema.json")):
        with path.open(encoding="utf-8") as fp:
            schema = json.load(fp)
        schemas[schema["$id"]] = schema
    return schemas


def build_registry(schemas: dict[str, Any]) -> Registry:
    resources = [
        (uri, Resource.from_contents(schema)) for uri, schema in schemas.items()
    ]
    return Registry().with_resources(resources)


class SchemaSet:
    """一次构建、多次复用的校验器集合。"""

    def __init__(self, schema_dir: Path = SCHEMA_DIR) -> None:
        self.schema_dir = schema_dir
        self.schemas = load_schemas(schema_dir)
        self.registry = build_registry(self.schemas)
        self._validators: dict[str, Draft202012Validator] = {}

    def validator(self, schema_file: str) -> Draft202012Validator:
        if schema_file not in self._validators:
            schema = self.schemas[f"https://schemas.dreamweave.local/{schema_file}"]
            self._validators[schema_file] = Draft202012Validator(
                schema,
                registry=self.registry,
                format_checker=FormatChecker(),
            )
        return self._validators[schema_file]

    def iter_errors(self, data: Any, schema_file: str) -> list[tuple[str, str]]:
        validator = self.validator(schema_file)
        errors: list[tuple[str, str]] = []
        for error in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
            pointer = "/".join(str(part) for part in error.absolute_path) or "/"
            errors.append((pointer, error.message))
        return errors
