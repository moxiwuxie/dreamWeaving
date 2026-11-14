"""织梦网页控制台：零依赖 HTTP 服务，用于边玩边开发。

启动后浏览器打开控制台（/console）或 3D 世界（/），可以推进时间、
推进剧情、重置世界、与 NPC 和物件交互。世界运行时状态封装在
:class:`dreamweave.webplay.Playground` 中，本模块只负责把 HTTP
请求翻译成对它的调用，并托管 play3d 静态资源与控制台页面。
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .world import WorldError

if TYPE_CHECKING:  # 仅类型标注用，运行时靠鸭子类型避免循环导入
    from .webplay import Playground

MAX_MINUTES = 1440 * 30
MAX_DAYS = 30

PACKAGE_DIR = Path(__file__).resolve().parent
PLAY3D_DIR = PACKAGE_DIR / "play3d"
CONSOLE_HTML = PACKAGE_DIR / "console.html"

STATIC_MIME = {
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _static_asset(path: str) -> tuple[Path, str] | None:
    """把 /static/<name> 映射到 play3d 下的扁平文件，供 ES 模块加载。"""
    if not path.startswith("/static/"):
        return None
    name = path[len("/static/"):]
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    content_type = STATIC_MIME.get(Path(name).suffix.lower())
    if content_type is None:
        return None
    asset = PLAY3D_DIR / name
    if not asset.is_file():
        return None
    return asset, content_type


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _handler_factory(playground: "Playground") -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # 保持终端干净
            return

        def _send_text(self, body: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                page = PLAY3D_DIR / "index.html"
                if not page.is_file():
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "3D 页面缺失"})
                    return
                self._send_text(page.read_bytes(), "text/html; charset=utf-8")
                return
            if self.path == "/console":
                if not CONSOLE_HTML.is_file():
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "控制台页面缺失"})
                    return
                self._send_text(CONSOLE_HTML.read_bytes(), "text/html; charset=utf-8")
                return
            static = _static_asset(self.path)
            if static is not None:
                asset, content_type = static
                self._send_text(asset.read_bytes(), content_type)
                return
            if self.path == "/api/view":
                _json_response(self, HTTPStatus.OK, playground.view())
                return
            if self.path == "/api/state":
                _json_response(self, HTTPStatus.OK, playground.state())
                return
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw or b"{}")
                if self.path == "/api/tick":
                    minutes = min(MAX_MINUTES, max(1, int(payload.get("minutes", 60))))
                    step = min(1440, max(1, int(payload.get("step", 60))))
                    _json_response(self, HTTPStatus.OK, playground.tick(minutes, step))
                elif self.path == "/api/timeflow":
                    days = min(MAX_DAYS, max(1, int(payload.get("days", 1))))
                    step = min(1440, max(1, int(payload.get("step", 60))))
                    _json_response(self, HTTPStatus.OK, playground.timeflow(days, step))
                elif self.path == "/api/advance":
                    days = min(MAX_DAYS, max(1, int(payload.get("days", 1))))
                    _json_response(self, HTTPStatus.OK, playground.advance(days))
                elif self.path == "/api/act":
                    quest_id = str(payload.get("quest_id") or "")
                    if not quest_id:
                        raise ValueError("缺少 quest_id")
                    _json_response(self, HTTPStatus.OK, playground.act(quest_id))
                elif self.path == "/api/sensory":
                    events = payload.get("events")
                    if events is not None and not isinstance(events, (dict, list)):
                        raise ValueError("events 必须是对象或数组")
                    preset = payload.get("preset")
                    include_status = bool(payload.get("status", preset == "status"))
                    _json_response(
                        self,
                        HTTPStatus.OK,
                        playground.sensory_plan(
                            events=events,
                            preset=str(preset) if preset else None,
                            include_status=include_status,
                        ),
                    )
                elif self.path == "/api/interact":
                    target = str(payload.get("target") or "")
                    target_id = str(payload.get("id") or "")
                    action = str(payload.get("action") or "")
                    if not target or not target_id or not action:
                        raise ValueError("缺少 target / id / action")
                    position = payload.get("position_m")
                    if position is not None and not isinstance(position, list):
                        raise ValueError("position_m 必须是数组")
                    text = payload.get("text")
                    if text is not None and not isinstance(text, str):
                        raise ValueError("text 必须是字符串")
                    _json_response(
                        self,
                        HTTPStatus.OK,
                        playground.interact(
                            target, target_id, action, position_m=position, text=text
                        ),
                    )
                elif self.path == "/api/reset":
                    playground.reset()
                    _json_response(
                        self,
                        HTTPStatus.OK,
                        {"accepted": True, "message": "已重置世界", "state": playground.state()},
                    )
                else:
                    _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
            except (ValueError, TypeError, KeyError, WorldError) as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:  # 开发期直接暴露错误方便定位
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    return Handler


def create_server(
    playground: "Playground", *, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _handler_factory(playground))
