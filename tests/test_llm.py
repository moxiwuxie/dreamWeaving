"""M008 AI 剧情生成：输出解析、重试闭环与离线客户端。"""

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import dreamweave.llm as llm
from dreamweave.llm import HttpChatClient, LLMError, extract_json, generate_story
from dreamweave.narrative import build_story_context
from dreamweave.world import WorldSession

DEMO = Path(__file__).resolve().parent.parent / "examples" / "earth_demo"


def _copy_demo(tmp_path: Path) -> Path:
    target = tmp_path / "world"
    shutil.copytree(DEMO, target)
    return target


def _outline(story_key: str = "0123456789abcdef") -> dict:
    return {
        "story_key": story_key,
        "title": "井边的秤",
        "logline": "井水见底时，村口那杆秤成了新的争端。",
        "tensions": [
            {
                "id": "well_queue",
                "name": "井口排队",
                "parties": [
                    {"kind": "class", "ref": "farmers"},
                    {"kind": "class", "ref": "nobility"},
                ],
                "stake": "water",
                "stage": "emerging",
                "intensity": 0.5,
                "triggers": ["井水下降"],
            }
        ],
        "arcs": [
            {
                "id": "qiu_well_queue",
                "title": "排队的人",
                "npc_ref": "elder_qiu",
                "desire": "让每户都能分到水",
                "obstacle": "贵族家的长工插队",
                "flaw": "不愿得罪人",
                "secret": "他曾在夜里多打了一桶",
                "stakes": "community",
                "stage": "setup",
                "tags": ["水", "乡土"],
            }
        ],
        "chronicle": [
            {
                "id": "well_queue_day2",
                "day": 2,
                "summary": "井水下降，村口第一次因排队发生推搡。",
                "participants": ["farmers", "nobility"],
                "tension_ref": "well_queue",
            }
        ],
    }


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_extract_json_strips_fences_and_prose():
    text = "这是剧情：\n```json\n" + json.dumps(_outline(), ensure_ascii=False) + "\n```\n希望有用。"
    payload = extract_json(text)
    assert payload["title"] == "井边的秤"


def test_extract_json_ignores_braces_inside_strings():
    payload = extract_json('前缀 {"a": "括号 { } 不算", "b": {"c": 1}} 后缀')
    assert payload == {"a": "括号 { } 不算", "b": {"c": 1}}


@pytest.mark.parametrize("text", ["", "没有 JSON", '{"a": 1', '{"a": 1,}'])
def test_extract_json_rejects_invalid_output(text):
    with pytest.raises(LLMError):
        extract_json(text)


def test_generate_story_accepts_fenced_outline_without_writing(tmp_path):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    story_key = build_story_context(session)["story_key"]
    before = len(session.modules["narrative"]["tensions"])
    client = FakeClient(["```json\n" + json.dumps(_outline(story_key), ensure_ascii=False) + "\n```"])

    result = generate_story(session, client=client, dry_run=True)

    assert result["accepted"] is True, result["errors"]
    assert result["attempts"] == 1
    assert result["story_key"] == story_key
    assert len(session.modules["narrative"]["tensions"]) == before


def test_generate_story_repairs_once_and_then_writes(tmp_path):
    world = _copy_demo(tmp_path)
    session = WorldSession(world)
    story_key = build_story_context(session)["story_key"]
    client = FakeClient(["{\"story_key\": \"bad\"}", json.dumps(_outline(story_key), ensure_ascii=False)])

    result = generate_story(session, client=client, max_repairs=1)

    assert result["accepted"] is True, result["errors"]
    assert result["attempts"] == 2
    assert "上一次输出被拒绝" in client.prompts[1]
    reloaded = WorldSession(world).modules["narrative"]
    assert any(item["id"] == "well_queue" for item in reloaded["tensions"])


def test_generate_story_rejects_mismatched_story_key(tmp_path):
    session = WorldSession(_copy_demo(tmp_path))
    story_key = build_story_context(session)["story_key"]
    client = FakeClient(
        [
            json.dumps(_outline("ffffffffffffffff"), ensure_ascii=False),
            json.dumps(_outline(story_key), ensure_ascii=False),
        ]
    )

    result = generate_story(session, client=client, max_repairs=1, dry_run=True)

    assert result["accepted"] is True, result["errors"]
    assert result["attempts"] == 2
    assert "story_key 必须为" in client.prompts[1]


def test_generate_story_rejects_outline_without_pressure_stake(tmp_path):
    session = WorldSession(_copy_demo(tmp_path))
    story_key = build_story_context(session)["story_key"]
    outline = _outline(story_key)
    outline["tensions"][0]["stake"] = "honor"

    client = FakeClient([json.dumps(outline, ensure_ascii=False)] * 2)
    result = generate_story(session, client=client, max_repairs=1, dry_run=True)

    assert result["accepted"] is False
    assert result["attempts"] == 2
    assert result["errors"][0]["code"] == "OUTLINE_PRESSURE"
    assert "结构压力诉求" in client.prompts[1]


def test_generate_story_rejects_reused_title(tmp_path):
    session = WorldSession(_copy_demo(tmp_path))
    story_key = build_story_context(session)["story_key"]
    outline = _outline(story_key)
    outline["title"] = "城门下的秤"

    client = FakeClient([json.dumps(outline, ensure_ascii=False)] * 2)
    result = generate_story(session, client=client, max_repairs=1, dry_run=True)

    assert result["accepted"] is False
    assert result["errors"][0]["code"] == "OUTLINE_TITLE_REUSED"


def test_generate_story_gives_up_after_repairs(tmp_path):
    session = WorldSession(_copy_demo(tmp_path))
    client = FakeClient(["不是 JSON", "仍然不是 JSON"])

    result = generate_story(session, client=client, max_repairs=1)

    assert result["accepted"] is False
    assert result["attempts"] == 2
    assert result["errors"][0]["code"] == "LLM_ERROR"
    assert result["result"] is None


def test_http_client_requires_configuration(monkeypatch):
    for key in ("DREAMWEAVE_LLM_BASE_URL", "DREAMWEAVE_LLM_API_KEY", "DREAMWEAVE_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(LLMError):
        HttpChatClient()

    client = HttpChatClient(base_url="http://127.0.0.1:1234/v1/", model="local")
    assert client.endpoint == "http://127.0.0.1:1234/v1/chat/completions"
    assert llm.TEMPERATURE > 0


class _StubHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    fail = False

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        )
        if type(self).fail:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        reply = json.dumps({"ok": True}, ensure_ascii=False)
        payload = json.dumps({"choices": [{"message": {"content": reply}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # noqa: A002 - 覆盖基类签名
        return


def test_http_client_sends_openai_compatible_request():
    _StubHandler.requests = []
    _StubHandler.fail = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = HttpChatClient(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="secret",
            model="test-model",
            timeout=5,
        )
        assert json.loads(client.complete("你好")) == {"ok": True}

        request = _StubHandler.requests[-1]
        assert request["path"] == "/v1/chat/completions"
        assert request["auth"] == "Bearer secret"
        assert request["body"]["model"] == "test-model"
        assert request["body"]["temperature"] == llm.TEMPERATURE
        assert request["body"]["messages"] == [{"role": "user", "content": "你好"}]

        _StubHandler.fail = True
        with pytest.raises(LLMError):
            client.complete("再试一次")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
