"""M001-04 / M005-04 .world 存档包：导出、导入、版本兼容与完整性校验。

包格式为 zip，根目录 world.json + 相对路径的模块配置与资源。
导出前必须通过全量校验；导出时刷新各模块 sha256 与清单自身摘要。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .schemas import MANIFEST_SCHEMA, SchemaSet
from .validator import _find_manifest, _sha256, validate_world

LOADER_VERSION = "0.1.0"
SUPPORTED_SCHEMA_VERSION = "0.1.0"
MANIFEST_NAME = "world.json"


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _parse_version(version: str) -> tuple[int, int, int]:
    try:
        major, minor, patch = version.split(".")
        return int(major), int(minor), int(patch)
    except ValueError:
        return (0, 0, 0)


def check_compatibility(manifest: dict[str, Any], loader_version: str = LOADER_VERSION) -> list[dict[str, str]]:
    """版本兼容：清单 Schema 版本必须一致，加载器不得低于存档要求。"""
    issues: list[dict[str, str]] = []
    schema_version = manifest.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        issues.append(
            _issue(
                "SCHEMA_VERSION_UNSUPPORTED",
                f"存档 schema_version={schema_version}，当前加载器仅支持 {SUPPORTED_SCHEMA_VERSION}",
            )
        )

    required = manifest.get("engine", {}).get("min_loader_version", "0.0.0")
    if _parse_version(loader_version) < _parse_version(str(required)):
        issues.append(
            _issue(
                "LOADER_TOO_OLD",
                f"存档要求加载器 >= {required}，当前为 {loader_version}",
            )
        )
    return issues


def manifest_checksum(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "checksum"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _refresh_checksums(root: Path, manifest: dict[str, Any]) -> None:
    for ref in (manifest.get("modules") or {}).values():
        rel = ref.get("path")
        path = root / rel if isinstance(rel, str) else None
        if path is not None and path.is_file():
            ref["sha256"] = _sha256(path)
    for asset in manifest.get("assets") or []:
        path = root / asset.get("path", "")
        if path.is_file():
            asset["sha256"] = _sha256(path)
            asset["bytes"] = path.stat().st_size


def pack_world(root: str | Path, output: str | Path) -> dict[str, Any]:
    """校验 -> 刷新摘要 -> 打包。返回结构化结果，失败不产出残缺包。"""
    root_path = Path(root).expanduser().resolve()
    manifest_path = _find_manifest(root_path)
    if manifest_path is None:
        return {"ok": False, "package": None, "errors": [_issue("MANIFEST_NOT_FOUND", f"未找到 world.json：{root_path}")], "warnings": []}

    report = validate_world(root_path)
    if not report.ok:
        return {
            "ok": False,
            "package": None,
            "errors": [issue.to_dict() for issue in report.errors],
            "warnings": [issue.to_dict() for issue in report.warnings],
        }

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_checksums(root_path, manifest)
    manifest["checksum"] = manifest_checksum(manifest)
    # 摘要只写入包内清单，不改动工作目录——否则导出后继续编辑模块会立刻校验失败。
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for ref in (manifest.get("modules") or {}).values():
        rel = ref.get("path")
        if isinstance(rel, str):
            files.append(root_path / rel)
    for asset in manifest.get("assets") or []:
        files.append(root_path / asset.get("path", ""))

    missing = [str(path.relative_to(root_path)) for path in files if not path.is_file()]
    if missing:
        return {"ok": False, "package": None, "errors": [_issue("PACKAGE_FILE_MISSING", f"缺少文件：{', '.join(missing)}")], "warnings": []}

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(manifest_path.name, manifest_bytes)
        for path in dict.fromkeys(files):
            archive.write(path, path.relative_to(root_path).as_posix())

    return {
        "ok": True,
        "package": str(output_path),
        "modules": len(manifest.get("modules") or {}),
        "assets": len(manifest.get("assets") or []),
        "checksum": manifest["checksum"],
        "warnings": [issue.to_dict() for issue in report.warnings],
        "errors": [],
    }


def _safe_entries(archive: zipfile.ZipFile) -> tuple[dict[str, bytes], list[dict[str, str]]]:
    """读取全部条目到内存，拒绝绝对路径与路径穿越。"""
    entries: dict[str, bytes] = {}
    issues: list[dict[str, str]] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or name.startswith("\\"):
            issues.append(_issue("ZIP_UNSAFE_PATH", f"包内存在不安全路径：{name}"))
            continue
        entries[name] = archive.read(info)
    return entries, issues


def inspect_package(package: str | Path) -> dict[str, Any]:
    """不落盘，直接读取包内清单并给出兼容性结论。"""
    package_path = Path(package).expanduser().resolve()
    if not package_path.is_file():
        return {"ok": False, "errors": [_issue("PACKAGE_NOT_FOUND", str(package_path))]}

    try:
        with zipfile.ZipFile(package_path) as archive:
            entries, issues = _safe_entries(archive)
    except zipfile.BadZipFile:
        return {"ok": False, "errors": [_issue("PACKAGE_CORRUPT", "不是合法的 zip/.world 文件")]}

    if issues:
        return {"ok": False, "errors": issues}
    if MANIFEST_NAME not in entries:
        return {"ok": False, "errors": [_issue("MANIFEST_NOT_FOUND", f"包内缺少 {MANIFEST_NAME}")]}

    manifest = json.loads(entries[MANIFEST_NAME].decode("utf-8"))
    issues = check_compatibility(manifest)
    return {
        "ok": not issues,
        "world_id": manifest.get("world_id"),
        "world_name": manifest.get("world_name"),
        "world_version": manifest.get("world_version"),
        "template": manifest.get("template"),
        "modules": sorted((manifest.get("modules") or {}).keys()),
        "assets": len(manifest.get("assets") or []),
        "loader_version": LOADER_VERSION,
        "errors": issues,
    }


def unpack_world(package: str | Path, dest: str | Path) -> dict[str, Any]:
    """解包并校验：路径安全 -> 版本兼容 -> 摘要一致 -> 全量校验 -> 落盘。"""
    package_path = Path(package).expanduser().resolve()
    if not package_path.is_file():
        return {"ok": False, "path": None, "errors": [_issue("PACKAGE_NOT_FOUND", str(package_path))]}

    try:
        with zipfile.ZipFile(package_path) as archive:
            entries, issues = _safe_entries(archive)
    except zipfile.BadZipFile:
        return {"ok": False, "path": None, "errors": [_issue("PACKAGE_CORRUPT", "不是合法的 zip/.world 文件")]}

    if issues:
        return {"ok": False, "path": None, "errors": issues}
    if MANIFEST_NAME not in entries:
        return {"ok": False, "path": None, "errors": [_issue("MANIFEST_NOT_FOUND", f"包内缺少 {MANIFEST_NAME}")]}

    manifest = json.loads(entries[MANIFEST_NAME].decode("utf-8"))
    issues = check_compatibility(manifest)

    for key, ref in (manifest.get("modules") or {}).items():
        rel, expected = ref.get("path"), ref.get("sha256")
        if rel not in entries:
            issues.append(_issue("MODULE_MISSING", f"{key} 配置缺失：{rel}"))
        elif expected and hashlib.sha256(entries[rel]).hexdigest() != expected:
            issues.append(_issue("CHECKSUM_MISMATCH", f"{key} 摘要与清单不一致：{rel}"))

    if manifest.get("checksum") and manifest_checksum(manifest) != manifest.get("checksum"):
        issues.append(_issue("MANIFEST_CHECKSUM", "清单自身摘要不一致，文件可能被篡改"))

    if issues:
        return {"ok": False, "path": None, "errors": issues}

    dest_path = Path(dest).expanduser().resolve()
    if dest_path.exists() and any(dest_path.iterdir()):
        return {"ok": False, "path": None, "errors": [_issue("DEST_NOT_EMPTY", f"目标目录非空：{dest_path}")]}
    dest_path.mkdir(parents=True, exist_ok=True)

    for name, payload in entries.items():
        target = dest_path / PurePosixPath(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    report = validate_world(dest_path)
    if not report.ok:
        return {
            "ok": False,
            "path": str(dest_path),
            "errors": [issue.to_dict() for issue in report.errors],
            "warnings": [issue.to_dict() for issue in report.warnings],
        }

    return {
        "ok": True,
        "path": str(dest_path),
        "modules": sorted((manifest.get("modules") or {}).keys()),
        "warnings": [issue.to_dict() for issue in report.warnings],
        "errors": [],
    }
