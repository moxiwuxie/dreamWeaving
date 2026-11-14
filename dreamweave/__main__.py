"""命令行入口：校验、查询、自然语言改写、NPC 上下文构建。"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .llm import LLMError, generate_story
from .narrative import advance_narrative, build_story_context, handle_story_outline, narrative_pressures
from .npc_context import build_npc_context
from .npc_intent import handle_npc_reply
from .npc_schedule import build_schedule
from .packaging import inspect_package, pack_world, unpack_world
from .panel import build_panel
from .perception import build_perception
from .schemas import MODULE_SCHEMAS, RUNTIME_SCHEMAS, SCHEMA_DIR
from .scenes import CATEGORIES, derive_physics, derive_sensory, get_scene, list_scenes, unified_model
from .sensory_bus import SensoryBusError, build_sensory_plan, bus_config, describe_bus
from .simulation import Simulation
from .templates import TEMPLATES, create_world
from .translator import translate
from .validator import Report, validate_world
from .webplay import Playground, create_server
from .world import WorldError, WorldSession


def _lan_ip() -> str:
    """取本机在局域网中的地址，用于打印手机访问链接。"""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _print_text(report: Report) -> None:
    status = "PASS" if report.ok else "FAIL"
    print(f"[{status}] {report.target}")
    if report.loaded_modules:
        print(f"  已加载模块: {', '.join(report.loaded_modules)}")
    for issue in report.errors:
        print(f"  ERROR {issue.code} @ {issue.path}\n        {issue.message}")
    for issue in report.warnings:
        print(f"  WARN  {issue.code} @ {issue.path}\n        {issue.message}")
    print(f"  结果: {len(report.errors)} 个错误, {len(report.warnings)} 个警告")


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_world(args.target)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    if not report.ok:
        return 1
    if args.strict and report.warnings:
        return 2
    return 0


def _cmd_schemas() -> int:
    for module, schema_file in MODULE_SCHEMAS.items():
        print(f"{module:12s} {SCHEMA_DIR / schema_file}")
    print(f"{'manifest':12s} {SCHEMA_DIR / 'world_manifest.schema.json'}")
    print(f"{'npc_intent':12s} {SCHEMA_DIR / 'npc_intent.schema.json'}")
    for name, schema_file in RUNTIME_SCHEMAS.items():
        print(f"{name:12s} {SCHEMA_DIR / schema_file}")
    print(f"{'common':12s} {SCHEMA_DIR / 'common.schema.json'}")
    return 0


def _print_panel_fields(fields: list[dict], depth: int = 1) -> None:
    pad = "  " * depth
    for item in fields:
        meta: list[str] = []
        if item["widget"] in ("number", "integer", "slider"):
            low = item.get("minimum", "-∞")
            high = item.get("maximum", "+∞")
            meta.append(f"[{low}, {high}]")
        if item.get("options"):
            meta.append("|".join(str(option) for option in item["options"]))
        if "value" in item:
            meta.append(f"固定 {item['value']}")
        if item["widget"] == "list":
            span = f"{item['min_items']}~{item.get('max_items', '∞')} 项"
            meta.append(span)
        meta.append("必填" if item["required"] else "可选")
        print(f"{pad}{item['key']:26s} {item['widget']:8s} {' '.join(meta)}")
        if item.get("description"):
            print(f"{pad}  └ {item['description']}")
        if item.get("fields"):
            _print_panel_fields(item["fields"], depth + 1)
        if item.get("item_fields"):
            print(f"{pad}  └ 每项字段:")
            _print_panel_fields(item["item_fields"], depth + 2)


def _cmd_panel(args: argparse.Namespace) -> int:
    panel = build_panel(args.module)
    if args.json:
        print(json.dumps(panel, ensure_ascii=False, indent=2))
        return 0
    print(f"[PANEL] {panel['module']} — {panel['title']}（{panel['schema']}）")
    if panel["description"]:
        print(f"  {panel['description']}")
    _print_panel_fields(panel["fields"])
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    session = WorldSession(args.target)
    if args.what == "physics":
        data = session.get_world_physics()
    elif args.what == "npcs":
        data = session.query_npc_list(args.layer, args.scene)
    elif args.what == "biomes":
        data = session.query_biome_stats()
    elif args.what == "scenes":
        data = session.query_scenes()
    elif args.what == "scene-at":
        if not args.position:
            print("[FAIL] scene-at 需要 --position X Y Z（米制世界坐标）", file=sys.stderr)
            return 2
        data = session.locate_scene(args.position)
    else:
        data = session.get_weather()

    if args.what == "scene-at":
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        x, y, z = data["position_m"]
        cx, cy, cz = data["chunk"]
        realm = "世界内" if data["inside_world"] else "越界"
        print(f"[LOCATE] ({x:g}, {y:g}, {z:g}) m -> chunk ({cx}, {cy}, {cz}) · {realm}")
        scene = data["scene"]
        if scene is None:
            print("  未命中任何场景")
            return 0
        uef = scene["uef"]
        print(f"  场景 {scene['id']} — {scene['name']}（{scene['category']}）")
        print(f"  重力 {uef['gravity']} m/s² · 介质 {uef['medium']['kind']} · 温度 {uef['energy']['ambient_temperature_c']}℃")
        if scene["hazards"]:
            labels = ", ".join(f"{item['kind']}({item['severity']})" for item in scene["hazards"])
            print(f"  危害 {labels}")
        return 0

    if args.json or not isinstance(data, list):
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.what == "scenes":
        for item in data:
            low = ",".join(str(value) for value in item["bounds"]["min_chunk"])
            high = ",".join(str(value) for value in item["bounds"]["max_chunk"])
            size = "×".join(
                str(round(item["world_bounds_m"]["max"][axis] - item["world_bounds_m"]["min"][axis], 3))
                for axis in range(3)
            )
            print(
                f"  {item['category']:16s} {item['id']:22s} {item['name']:12s} "
                f"chunk[{low}]~[{high}] {size}m 介质={item['medium']} 重力={item['gravity']}"
            )
        print(f"共 {len(data)} 个场景")
        return 0

    for item in data:
        print(json.dumps(item, ensure_ascii=False))
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    session = WorldSession(args.target)
    result = translate(session, args.text, dry_run=not args.apply)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "REJECTED" if not result["accepted"] else ("APPLIED" if args.apply else "DRY-RUN")
        print(f"[{status}] {result['message']}")
        for index, operation in enumerate(result["operations"], start=1):
            print(
                f"  {index}. {operation.get('op')} {operation.get('path')} = "
                f"{json.dumps(operation.get('value'), ensure_ascii=False)}"
            )
            if operation.get("reason"):
                print(f"     理由: {operation['reason']}")
        for issue in result["errors"]:
            print(f"  ERROR {issue['code']} @ {issue['path']}\n        {issue['message']}")
        for issue in result["warnings"]:
            print(f"  WARN  {issue['code']} @ {issue['path']}\n        {issue['message']}")

    return 0 if result["accepted"] else 1


def _cmd_npc_context(args: argparse.Namespace) -> int:
    session = WorldSession(args.target)
    try:
        payload = build_npc_context(session, args.npc_id, day=args.day, minute=args.minute)
    except (KeyError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload["prompt"])
    return 0


def _cmd_npc_reply(args: argparse.Namespace) -> int:
    session = WorldSession(args.target)
    raw = args.payload
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[ERROR] 回复 JSON 解析失败：{exc}", file=sys.stderr)
        return 2

    result = handle_npc_reply(session, args.npc_id, payload, dry_run=not args.apply)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "ACCEPTED" if result["accepted"] else "REJECTED"
        print(f"[{status}] {result['message']}")
        print(f"  台词: {result['dialogue']}")
        print(f"  意图: {result['action_type']}")
        for index, operation in enumerate(result["operations"], start=1):
            print(f"  {index}. {operation.get('op')} {operation.get('path')} = {json.dumps(operation.get('value'), ensure_ascii=False)}")
        for issue in result["errors"]:
            print(f"  ERROR {issue['code']} @ {issue['path']}\n        {issue['message']}")
        for issue in result["warnings"]:
            print(f"  WARN  {issue['code']} @ {issue['path']}\n        {issue['message']}")
    return 0 if result["accepted"] else 1


def _cmd_simulate(args: argparse.Namespace) -> int:
    session = WorldSession(args.target)
    try:
        if args.state_in:
            state = json.loads(args.state_in.read_text(encoding="utf-8"))
            simulation = Simulation.from_state(session, state, seed=args.seed)
        else:
            simulation = Simulation(
                session,
                day=args.day,
                position_m=args.position,
                seed=args.seed,
            )
        snapshot = simulation.tick(args.minutes, args.step)
    except (OSError, ValueError, KeyError) as exc:
        print(f"[FAIL] 无法恢复模拟状态：{exc}", file=sys.stderr)
        return 2

    if args.state_out:
        args.state_out.write_text(
            json.dumps(simulation.to_state(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps({"snapshot": snapshot, "events": simulation.events}, ensure_ascii=False, indent=2))
        return 0

    clock = snapshot["clock"]
    player = snapshot["player"]
    weather = snapshot["weather"]
    scene = snapshot["scene"]
    print(f"[SIM] {args.target} · 第 {clock['day']} 天 {clock['time']}（步长 {args.step} 分钟）")
    if scene:
        print(f"  场景 {scene['id']} — {scene['name']}（{scene['category']}）")
    print(
        f"  环境 {snapshot['ambient_temperature_c']}℃ · 天气 "
        f"{weather['kind'] if weather else 'clear'}"
    )
    print(
        f"  体温 {player['core_temperature_c']}℃ · 饱腹 {player['hunger']} · 水分 {player['thirst']} · "
        f"疲劳 {player['fatigue']} · 氧气 {player['oxygen_seconds']}s · 生命 {player['health']}"
    )
    if snapshot["effects"]:
        labels = ", ".join(f"{item['id']}({item['severity']})" for item in snapshot["effects"])
        print(f"  生效状态 {labels}")
    survival = snapshot.get("survival") or {}
    if survival:
        actions = [
            label for key, label in (("drank", "饮水"), ("ate", "进食")) if survival.get(key)
        ]
        sources = [label for key, label in (("water", "水源"), ("food", "食物")) if survival.get(key)]
        print(
            f"  生存 {'+'.join(actions) if actions else '未补给'}"
            f" · 可用 {'/'.join(sources) if sources else '无'}"
        )
    npcs = snapshot.get("npcs") or []
    if npcs:
        sheltered = sum(1 for npc in npcs if npc["status"] == "sheltered")
        print(f"  NPC {len(npcs)} 人 · 避险 {sheltered} 人")
        for npc in npcs:
            print(
                f"    [{npc['layer']}] {npc['npc_id']} · {npc['activity']} @ {npc['location']}"
                f" → {npc['scene_id'] or '无场景'}"
            )
    ecology = snapshot.get("ecology") or []
    if ecology:
        print(
            "  生态 "
            + "，".join(
                f"{item['name']} {item['population']:.2f}"
                + ("" if item["status"] == "stable" else f"（{item['status']}）")
                for item in ecology
            )
        )
    for event in simulation.events:
        print(f"  · 第 {event['minute']} 分钟 {event['type']} → {event['value']}")
    return 0


def _cmd_play(args: argparse.Namespace) -> int:
    try:
        hour_text, _, minute_text = str(args.start_time).partition(":")
        start_minute = (int(hour_text) * 60 + int(minute_text or 0)) % 1440
    except ValueError:
        print(f"[FAIL] 开局时刻格式应为 HH:MM：{args.start_time}", file=sys.stderr)
        return 2
    try:
        playground = Playground(
            args.target,
            position_m=args.position,
            seed=args.seed,
            start_minute=start_minute,
        )
        server = create_server(playground, host=args.host, port=args.port)
    except (OSError, WorldError) as exc:
        print(f"[FAIL] 无法启动本地控制台：{exc}", file=sys.stderr)
        return 2
    port = server.server_address[1]
    local_url = f"http://127.0.0.1:{port}/"
    print(f"[PLAY] {local_url}")
    if args.host in ("0.0.0.0", "::"):
        print(f"  手机访问 http://{_lan_ip()}:{port}/ （手机与电脑需在同一局域网）")
    print(f"  文字控制台 {local_url}console")
    print(f"  世界 {args.target} · 出生点 {playground.spawn}")
    print("  Ctrl+C 停止服务")
    if not args.no_browser:
        webbrowser.open(local_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[PLAY] 已停止")
    finally:
        server.server_close()
    return 0


def _cmd_perception(args: argparse.Namespace) -> int:
    session = WorldSession(args.target)
    try:
        data = build_perception(
            session,
            perspective=args.perspective,
            position_m=args.position,
            scene_id=args.scene,
            yaw_deg=args.yaw,
            pitch_deg=args.pitch,
            status_ids=args.status,
        )
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    camera = data["visual"]["camera"]
    renderer = data["visual"]["renderer"]
    audio = data["audio"]
    backend = audio["backend"]
    position = ", ".join(f"{value:g}" for value in data["transform"]["position_m"])
    scene = data["visual"]["scene"]
    scene_label = f"{scene['id']}（{scene['medium']}）" if scene else "未命中场景"
    layers = ", ".join(f"{item['id']}={item['gain']}" for item in audio["layers"]) or "无"
    interfaces = ", ".join(data["interfaces"])
    print(f"[PERCEPTION] {data['world_id']} · {data['perspective']}")
    print(f"  相机 ({position}) m · FOV {camera['fov_deg']}° · 近 {camera['near_clip_m']} / 远 {camera['far_clip_m']} m")
    print(
        f"  视觉 {renderer.get('backend')} + {renderer.get('global_illumination')} + {renderer.get('geometry')}"
        f" · {renderer.get('anti_aliasing')} · {renderer.get('upscaler')} · 场景 {scene_label}"
    )
    print(
        f"  听觉 {backend.get('spatializer')} + {backend.get('mixer')} · HRTF "
        f"{'on' if audio['hrtf']['enabled'] else 'off'} · 混响 {audio['reverb']['preset']}"
        f"（{audio['reverb']['source']}）· 分层 {layers}"
    )
    print(f"  接口 {interfaces}（仅输出契约，未硬渲染）")
    return 0


def _cmd_sensory(args: argparse.Namespace) -> int:
    session = WorldSession(args.target)

    if not args.events and not args.status:
        data = describe_bus(bus_config(session))
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        print(
            f"[BUS] {session.manifest.get('world_id')} · 同步窗 {data['sync_window_ms']}ms / "
            f"软窗 {data['soft_sync_window_ms']}ms · {data['tick_hz']}Hz"
        )
        for channel, conf in data["channels"].items():
            devices = ", ".join(
                f"{device['id']}({'on' if device['enabled'] else 'off'},{device['latency_ms']:g}ms)"
                for device in conf["devices"]
            ) or "无设备"
            print(f"  {channel:7s} {conf['sync_mode']:4s} {'启用' if conf['enabled'] else '关闭'} · {devices}")
        policy = data["focus_policy"]
        print(
            f"  焦点降级 {'on' if policy.get('enabled') else 'off'} · 折扣 {policy.get('off_focus_scale')} · "
            f"衰减 {', '.join(policy.get('attenuated_channels') or []) or '无'}"
        )
        return 0

    events: list[dict] = []
    if args.events:
        raw = args.events
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"[ERROR] 事件 JSON 解析失败：{exc}", file=sys.stderr)
            return 2
        events = payload if isinstance(payload, list) else [payload]

    try:
        result = build_sensory_plan(session, events, include_status_effects=args.status, t_ms=args.t_ms)
    except SensoryBusError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        for issue in exc.errors:
            print(f"  {issue['path']}: {issue['message']}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    diagnostics = result["diagnostics"]
    print(
        f"[PLAN] 事件 {diagnostics['events_planned']} · 拒绝 {diagnostics['events_rejected']} · "
        f"派发 {diagnostics['deliveries']} · 丢弃 {diagnostics['dropped']} · "
        f"同步降级 {diagnostics['sync_violations']} · 硬通道最大偏移 {diagnostics['max_hard_skew_ms']}ms"
    )
    for plan in result["events"]:
        if not plan["deliveries"]:
            reasons = "，".join(
                f"{drop.get('channel') or 'event'}:{drop['reason']}" for drop in plan["dropped"]
            )
            print(f"  · {plan['event_id']} [{plan['kind']}] 无派发（{reasons or '无通道'}）")
            continue
        parts: list[str] = []
        seen: set[tuple[str, float]] = set()
        for delivery in plan["deliveries"]:
            key = (delivery["channel"], delivery["at_ms"])
            if key in seen:
                continue
            seen.add(key)
            parts.append(f"{delivery['channel']}@{delivery['at_ms']:g}ms")
        print(
            f"  · {plan['event_id']} [{plan['kind']}] "
            f"{'焦点' if plan['focus'] else '边缘'} → {' '.join(parts)}"
        )
    for rejected in result["rejected"]:
        messages = "；".join(f"{item['path']} {item['message']}" for item in rejected["errors"])
        print(f"  ERROR {rejected['event_id']}: {messages}")
    return 0 if result["ok"] else 1


def _cmd_npc_schedule(args: argparse.Namespace) -> int:
    session = WorldSession(args.target)
    try:
        data = build_schedule(
            session,
            day=args.day,
            minute=args.minute,
            layer=args.layer,
            scene_id=args.scene,
            activity=args.activity,
        )
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print(f"[SCHEDULE] 第 {data['day']} 天 {data['time']} · {data['count']} 人")
    for npc in data["npcs"]:
        location = npc["location"] or "无日程"
        scene = npc["scene_id"] or "不在场景"
        remain = f"，剩 {npc['ends_in_min']} 分钟" if npc["ends_in_min"] is not None else ""
        print(f"  [{npc['layer']:9s}] {npc['npc_id']:16s} {npc['name']} · {npc['activity']} @ {location} → {scene}{remain}")
    return 0


def _cmd_narrative(args: argparse.Namespace) -> int:
    session = WorldSession(args.target)

    if args.action == "pressures":
        data = narrative_pressures(session, limit=args.limit)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        for index, item in enumerate(data, start=1):
            drivers = ", ".join(f"{driver['key']}={driver['value']}" for driver in item["drivers"])
            print(
                f"  {index}. [{item['kind']:7s}] {item['id']:18s} 压力={item['score']:<6} "
                f"建议矛盾={item['suggested_stake']:8s} {drivers}"
            )
        return 0

    if args.action == "context":
        try:
            payload = build_story_context(
                session,
                nation_id=args.nation_id,
                class_id=args.class_id,
                npc_id=args.npc_id,
                scene_id=args.scene_id,
                day=args.day,
            )
        except ValueError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload["prompt"])
        return 0

    if args.action == "advance":
        try:
            state = (
                json.loads(args.state_in.read_text(encoding="utf-8")) if args.state_in else None
            )
            result = advance_narrative(
                session,
                days=args.days,
                day_start=args.day,
                seed=args.seed,
                dry_run=not args.apply,
                state=state,
            )
        except (OSError, ValueError, KeyError) as exc:
            print(f"[FAIL] 无法推进剧情：{exc}", file=sys.stderr)
            return 2
        if args.state_out and result["accepted"]:
            args.state_out.write_text(
                json.dumps(result["state"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            status = "APPLIED" if result["accepted"] and args.apply else ("REJECTED" if not result["accepted"] else "DRY-RUN")
            print(f"[{status}] {result['message']}")
            for item in result["weather"]:
                print(f"  · 第 {item['day']} 天 天气 {item['kind']}")
            for item in result.get("ecology", []):
                print(f"  · 第 {item['day']} 天 生态 {item['species_id']} -> {item['status']}")
            for item in result.get("shelter", []):
                print(f"  · 第 {item['day']} 天 避险 {len(item['npcs'])} 人：{'、'.join(item['npcs'])}")
            for change in result["changes"]:
                print(
                    f"  · 矛盾 {change['tension_id']}: {change['from_intensity']} -> {change['to_intensity']}"
                    f"（{change['from_stage']} -> {change['to_stage']}）"
                )
            for entry in result["chronicle"]:
                print(f"  · 编年史 {entry['id']}：{entry['summary']}")
            for issue in result["errors"]:
                print(f"  ERROR {issue['code']} @ {issue['path']}\n        {issue['message']}")
            for issue in result["warnings"]:
                print(f"  WARN  {issue['code']} @ {issue['path']}\n        {issue['message']}")
        return 0 if result["accepted"] else 1

    if args.action == "generate":
        try:
            result = generate_story(
                session,
                max_repairs=args.max_repairs,
                dry_run=not args.apply,
                nation_id=args.nation_id,
                class_id=args.class_id,
                npc_id=args.npc_id,
                scene_id=args.scene_id,
                day=args.day,
            )
        except (LLMError, ValueError) as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            outcome = result.get("result") or {}
            status = "REJECTED" if not result["accepted"] else ("APPLIED" if args.apply else "DRY-RUN")
            print(f"[{status}] 尝试 {result['attempts']} 次 · story_key={result['story_key']}")
            if result.get("title"):
                print(f"  {result['title']}：{result.get('logline')}")
            for index, operation in enumerate(outcome.get("operations", []), start=1):
                print(f"  {index}. {operation['op']} {operation['path']}")
            for issue in result["errors"]:
                print(f"  ERROR {issue['code']} @ {issue['path']}\n        {issue['message']}")
        return 0 if result["accepted"] else 1

    if not args.payload:
        print("[FAIL] narrative outline 需要传入 JSON 或 @文件", file=sys.stderr)
        return 2
    raw = args.payload
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    try:
        outline = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[ERROR] 大纲 JSON 解析失败：{exc}", file=sys.stderr)
        return 2

    result = handle_story_outline(session, outline, dry_run=not args.apply)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "REJECTED" if not result["accepted"] else ("APPLIED" if args.apply else "DRY-RUN")
        print(f"[{status}] {result['message']}")
        if result.get("title"):
            print(f"  {result['title']}：{result['logline']}")
        for index, operation in enumerate(result["operations"], start=1):
            print(f"  {index}. {operation['op']} {operation['path']}")
        for issue in result["errors"]:
            print(f"  ERROR {issue['code']} @ {issue['path']}\n        {issue['message']}")
        for issue in result["warnings"]:
            print(f"  WARN  {issue['code']} @ {issue['path']}\n        {issue['message']}")
    return 0 if result["accepted"] else 1


def _print_package_result(result: dict, title: str) -> int:
    status = "OK" if result.get("ok") else "FAILED"
    print(f"[{status}] {title}")
    for key in ("package", "path", "world_id", "world_name", "world_version", "checksum"):
        if result.get(key):
            print(f"  {key}: {result[key]}")
    if result.get("modules"):
        print(f"  modules: {result['modules'] if isinstance(result['modules'], int) else ', '.join(result['modules'])}")
    for issue in result.get("errors") or []:
        print(f"  ERROR {issue.get('code')} {issue.get('message')}")
    for issue in result.get("warnings") or []:
        print(f"  WARN  {issue.get('code')} {issue.get('message')}")
    return 0 if result.get("ok") else 1


def _cmd_pack(args: argparse.Namespace) -> int:
    output = args.output or Path.cwd() / f"{Path(args.target).name}.world"
    result = pack_world(args.target, output)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    return _print_package_result(result, f"导出 {args.target} -> {output}")


def _cmd_unpack(args: argparse.Namespace) -> int:
    result = unpack_world(args.package, args.output)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    return _print_package_result(result, f"导入 {args.package} -> {args.output}")


def _print_scene(scene: dict) -> None:
    uef = scene["uef"]
    medium = uef["medium"]
    energy = uef["energy"]
    terrain = uef["terrain"]
    resources = uef["resources"]
    society = uef["society"]
    print(f"[SCENE] {scene['id']} — {scene['name']}（{scene['category']}）")
    print(f"  轴1 重力场   {uef['gravity']} m/s²")
    print(
        f"  轴2 介质     {medium['kind']} · {medium['density_kg_m3']} kg/m³ · "
        f"{medium['pressure_kpa']} kPa · 声速 {medium['sound_speed_mps']} m/s"
    )
    print(
        f"  轴3 能量     {energy['ambient_temperature_c']}℃ · 辐照 {energy['solar_irradiance_scale']} · "
        f"辐射 {energy['radiation_scale']} · 梯度 {energy['thermal_gradient_c_per_m']}℃/m"
    )
    print(
        f"  轴4 地形     海拔 {terrain['elevation_m']} m · 起伏 {terrain['relief_m']} m · "
        f"坡度 {terrain['slope_deg']}° · 封闭度 {terrain['enclosure']}"
    )
    print(
        f"  轴5 资源     水 {resources['water_availability']} · 生物量 {resources['biomass']} · "
        f"矿产 {resources['mineral_richness']} · 氧 {resources['breathable_oxygen']}"
    )
    print(
        f"  轴6 社会     人口 {society['population_density_per_km2']}/km² · "
        f"建成度 {society['built_density']} · 秩序 {society['law_presence']}"
    )
    physics = derive_physics(scene["id"])
    print(f"  → 物理推导   gravity={physics['gravity']} 密度={physics['air_density']} 声速={physics['speed_of_sound']}")
    sensory = derive_sensory(scene["id"])
    print(f"  → 五感推导   混响={sensory['reverb_preset']} 遮挡={sensory['occlusion_attenuation']} 光照EV={sensory['ambient_light_ev']}")
    if scene.get("hazards"):
        labels = ", ".join(f"{h['kind']}({h['severity']})" for h in scene["hazards"])
        print(f"  → 派生危害   {labels}")


def _cmd_scenes(args: argparse.Namespace) -> int:
    if args.action == "model":
        model = unified_model()
        if args.json:
            print(json.dumps(model, ensure_ascii=False, indent=2))
            return 0
        print(f"[MODEL] {model['name']} — {model['summary']}")
        for axis in model["axes"]:
            print(f"  {axis['name']:6s} {axis['unit']:18s} → {'、'.join(axis['drives'])}")
        for rule in model["rules"]:
            print(f"  规则 {rule['name']}: {rule['statement']}")
        return 0

    if args.action == "list":
        entries = list_scenes(args.category)
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
            return 0
        for entry in entries:
            print(f"  {entry['category']:16s} {entry['id']:26s} {entry['name']}")
        print(f"共 {len(entries)} 个场景")
        return 0

    if not args.scene_id:
        print("[FAIL] 缺少场景 id，例如：scenes show city_core", file=sys.stderr)
        return 2
    scene = get_scene(args.scene_id)
    if args.json:
        print(json.dumps(
            {"scene": scene, "physics": derive_physics(args.scene_id), "sensory": derive_sensory(args.scene_id)},
            ensure_ascii=False,
            indent=2,
        ))
        return 0
    _print_scene(scene)
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    result = create_world(
        args.output,
        args.world_id,
        world_name=args.name,
        template=args.template,
        overwrite=args.force,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    return _print_package_result(result, f"创建 {args.template} 世界 -> {args.output}")


def _cmd_inspect(args: argparse.Namespace) -> int:
    result = inspect_package(args.package)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    return _print_package_result(result, f"检查 {args.package}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dreamweave", description="《织梦》世界配置工具链")
    parser.add_argument("--version", action="version", version=f"dreamweave {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="校验 .world 包或 world.json 清单")
    validate.add_argument("target", type=Path, help="世界包目录或 world.json 路径")
    validate.add_argument("--json", action="store_true", help="输出机器可读的 JSON 报告")
    validate.add_argument("--strict", action="store_true", help="存在警告时同样返回非零退出码")

    subparsers.add_parser("schemas", help="列出已注册的 Schema")

    panel = subparsers.add_parser("panel", help="M005-02 由 Schema 生成编辑器面板树")
    panel.add_argument("module", choices=["manifest", *MODULE_SCHEMAS], help="模块名")
    panel.add_argument("--json", action="store_true", help="输出机器可读的面板描述")

    scenes = subparsers.add_parser("scenes", help="场景库与统一环境场（UEF）")
    scenes.add_argument("action", choices=["list", "show", "model"], help="list 列表 / show 详情 / model 统一模型")
    scenes.add_argument("scene_id", nargs="?", help="show 时的场景 id")
    scenes.add_argument("--category", choices=list(CATEGORIES), help="list 时按分类过滤")
    scenes.add_argument("--json", action="store_true")

    query = subparsers.add_parser("query", help="M006-01 世界查询")
    query.add_argument("target", type=Path)
    query.add_argument("what", choices=["physics", "npcs", "biomes", "weather", "scenes", "scene-at"])
    query.add_argument("--layer", choices=["crowd", "mass_life", "llm_core"], help="仅查询指定 NPC 层")
    query.add_argument("--scene", help="仅查询常驻指定场景的 NPC")
    query.add_argument("--position", nargs=3, type=float, metavar=("X", "Y", "Z"), help="scene-at 的世界坐标（米）")
    query.add_argument("--json", action="store_true")

    simulate = subparsers.add_parser("simulate", help="M004 最小无头运行时：推进天气与玩家生理")
    simulate.add_argument("target", type=Path)
    simulate.add_argument("--minutes", type=int, default=1440, help="模拟总时长，默认 1 天")
    simulate.add_argument("--step", type=int, default=60, help="单步分钟数，默认 60")
    simulate.add_argument("--day", type=int, default=1, help="起始天数")
    simulate.add_argument("--position", nargs=3, type=float, metavar=("X", "Y", "Z"), help="玩家世界坐标（米）")
    simulate.add_argument("--seed", type=int, default=7, help="天气随机种子，默认 7")
    simulate.add_argument("--state-in", type=Path, help="从存档恢复运行状态（忽略 --day/--position）")
    simulate.add_argument("--state-out", type=Path, help="tick 结束后写出运行状态存档")
    simulate.add_argument("--json", action="store_true")

    play = subparsers.add_parser("play", help="本地网页控制台：边玩边开发")
    play.add_argument("target", type=Path)
    play.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    play.add_argument("--host", default="127.0.0.1", help="监听地址；手机同局域网访问传 0.0.0.0")
    play.add_argument("--position", nargs=3, type=float, metavar=("X", "Y", "Z"), help="出生坐标（米）")
    play.add_argument("--seed", type=int, default=7, help="天气随机种子，默认 7")
    play.add_argument("--start-time", default="08:00", help="开局时刻 HH:MM，默认 08:00")
    play.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    narrative = subparsers.add_parser("narrative", help="M007 剧情内核：结构压力、AI 上下文与大纲")
    narrative.add_argument("action", choices=["pressures", "context", "outline", "generate", "advance"])
    narrative.add_argument("target", type=Path)
    narrative.add_argument("payload", nargs="?", help="outline 的 JSON 字符串或 @文件路径")
    narrative.add_argument("--nation", dest="nation_id", help="聚焦国家 id")
    narrative.add_argument("--class", dest="class_id", help="聚焦阶层 id")
    narrative.add_argument("--npc", dest="npc_id", help="聚焦个体 id")
    narrative.add_argument("--scene", dest="scene_id", help="聚焦场景 id")
    narrative.add_argument("--day", type=int, default=1, help="剧情发生日 / advance 起始日")
    narrative.add_argument("--limit", type=int, default=5, help="压力条目上限")
    narrative.add_argument("--days", type=int, default=1, help="advance 推进天数")
    narrative.add_argument("--seed", type=int, default=7, help="advance 随机种子")
    narrative.add_argument("--state-in", type=Path, help="advance 从运行状态存档续跑（忽略 --day）")
    narrative.add_argument("--state-out", type=Path, help="advance 结束后写出运行状态存档")
    narrative.add_argument("--max-repairs", type=int, default=1, help="generate 校验失败后的重试次数")
    narrative.add_argument("--apply", action="store_true", help="通过校验后写盘")
    narrative.add_argument("--json", action="store_true")

    perception = subparsers.add_parser("perception", help="M009 感知层：第一/第三人称相机与视觉/听觉渲染契约")
    perception.add_argument("target", type=Path)
    perception.add_argument("--perspective", choices=["first_person", "third_person"], help="视角模式，默认取世界配置")
    perception.add_argument("--position", nargs=3, type=float, metavar=("X", "Y", "Z"), help="脚底世界坐标（米）")
    perception.add_argument("--scene", help="直接指定场景 id，用于验证混响与场景参数")
    perception.add_argument("--yaw", type=float, default=0.0, help="偏航角，0 指向 +X")
    perception.add_argument("--pitch", type=float, default=0.0, help="俯仰角，抬头为正")
    perception.add_argument("--status", action="append", default=[], help="生理状态 id，可重复")
    perception.add_argument("--json", action="store_true")

    sensory = subparsers.add_parser("sensory", help="M011 感官事件总线：路由、跨模态同步与降级")
    sensory.add_argument("target", type=Path)
    sensory.add_argument("--events", help="单个或数组事件 JSON，或用 @文件路径 读取")
    sensory.add_argument("--status", action="store_true", help="把玩家生理状态自动转成感知事件")
    sensory.add_argument("--t-ms", type=int, default=0, help="事件发生时间（毫秒），默认 0")
    sensory.add_argument("--json", action="store_true")

    npc_schedule = subparsers.add_parser("npc-schedule", help="M010 NPC 日程运行时：解析指定时刻的活动与场景")
    npc_schedule.add_argument("target", type=Path)
    npc_schedule.add_argument("--day", type=int, default=1, help="天数，默认 1")
    npc_schedule.add_argument("--minute", type=int, default=0, help="当天分钟数 0~1439，默认 0")
    npc_schedule.add_argument("--layer", choices=["crowd", "mass_life", "llm_core"], help="按 NPC 层级过滤")
    npc_schedule.add_argument("--scene", help="按解析后的场景 id 过滤")
    npc_schedule.add_argument("--activity", help="按活动过滤，如 sleep / eat / work / patrol")
    npc_schedule.add_argument("--json", action="store_true")

    ask = subparsers.add_parser("ask", help="M006-03 自然语言改写世界配置")
    ask.add_argument("target", type=Path)
    ask.add_argument("text", help="例如：把重力改为 -3.7")
    ask.add_argument("--apply", action="store_true", help="通过校验后真正写入配置；默认仅演练")
    ask.add_argument("--json", action="store_true")

    npc_context = subparsers.add_parser("npc-context", help="M006-04 构建 NPC 上下文与 Prompt")
    npc_context.add_argument("target", type=Path)
    npc_context.add_argument("npc_id")
    npc_context.add_argument("--day", type=int, help="注入当前日程的天数")
    npc_context.add_argument("--minute", type=int, help="注入当前日程的当天分钟数 0~1439")
    npc_context.add_argument("--json", action="store_true")

    npc_reply = subparsers.add_parser("npc-reply", help="M006-04 校验 NPC 回复并执行受控指令")
    npc_reply.add_argument("target", type=Path)
    npc_reply.add_argument("npc_id")
    npc_reply.add_argument("payload", help="npc_intent JSON 字符串，或用 @文件路径 读取")
    npc_reply.add_argument("--apply", action="store_true", help="通过校验后写入配置；默认仅演练")
    npc_reply.add_argument("--json", action="store_true")

    pack = subparsers.add_parser("pack", help="M005-04 导出 .world 存档包")
    pack.add_argument("target", type=Path)
    pack.add_argument("-o", "--output", type=Path, help="输出路径，默认 <目录名>.world")
    pack.add_argument("--json", action="store_true")

    unpack = subparsers.add_parser("unpack", help="M001-04 导入 .world 存档包")
    unpack.add_argument("package", type=Path)
    unpack.add_argument("-o", "--output", type=Path, required=True, help="解包目标目录（必须为空）")
    unpack.add_argument("--json", action="store_true")

    inspect = subparsers.add_parser("inspect", help="读取 .world 包清单与兼容性")
    inspect.add_argument("package", type=Path)
    inspect.add_argument("--json", action="store_true")

    init = subparsers.add_parser("init", help="M001-03 按模板创建世界")
    init.add_argument("world_id", help="小写蛇形世界 id，例如 my_world")
    init.add_argument("-o", "--output", type=Path, required=True, help="目标目录")
    init.add_argument("--template", choices=list(TEMPLATES), default="earth", help="模板，默认 earth")
    init.add_argument("--name", help="世界显示名，默认同 world_id")
    init.add_argument("--force", action="store_true", help="允许写入非空目录")
    init.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "schemas":
        return _cmd_schemas()
    try:
        if args.command == "validate":
            return _cmd_validate(args)
        if args.command == "panel":
            return _cmd_panel(args)
        if args.command == "scenes":
            return _cmd_scenes(args)
        if args.command == "query":
            return _cmd_query(args)
        if args.command == "simulate":
            return _cmd_simulate(args)
        if args.command == "play":
            return _cmd_play(args)
        if args.command == "perception":
            return _cmd_perception(args)
        if args.command == "sensory":
            return _cmd_sensory(args)
        if args.command == "narrative":
            return _cmd_narrative(args)
        if args.command == "ask":
            return _cmd_ask(args)
        if args.command == "npc-context":
            return _cmd_npc_context(args)
        if args.command == "npc-schedule":
            return _cmd_npc_schedule(args)
        if args.command == "npc-reply":
            return _cmd_npc_reply(args)
        if args.command == "pack":
            return _cmd_pack(args)
        if args.command == "unpack":
            return _cmd_unpack(args)
        if args.command == "init":
            return _cmd_init(args)
        return _cmd_inspect(args)
    except WorldError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
