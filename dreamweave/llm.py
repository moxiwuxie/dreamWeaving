"""M008 AI 剧情生成闭环：结构上下文 -> 真实 LLM -> 校验 -> 写入编年史。

只依赖标准库：默认对接任意 OpenAI 兼容的 /chat/completions 接口，
配置通过 DREAMWEAVE_LLM_BASE_URL / DREAMWEAVE_LLM_API_KEY / DREAMWEAVE_LLM_MODEL 注入。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

from .narrative import build_story_context, handle_story_outline, outline_quality_errors
from .world import WorldSession

DEFAULT_TIMEOUT_SECONDS = 60.0
TEMPERATURE = 0.8


class LLMError(RuntimeError):
    """LLM 配置缺失、请求失败或返回内容无法解析。"""


class ChatClient(Protocol):
    """可替换的对话客户端，便于离线测试。"""

    def complete(self, prompt: str) -> str: ...


def extract_json(text: str) -> Any:
    """从 LLM 输出中提取第一个完整的 JSON 对象，容忍 ``` 围栏与前后解释文字。"""
    if not isinstance(text, str) or not text.strip():
        raise LLMError("LLM 返回内容为空")

    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    start = raw.find("{")
    if start < 0:
        raise LLMError("LLM 返回内容中找不到 JSON 对象")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise LLMError(f"LLM 返回的 JSON 无法解析：{exc}") from exc
    raise LLMError("LLM 返回的 JSON 不完整")


class HttpChatClient:
    """OpenAI 兼容的最小 HTTP 客户端。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = (base_url or os.environ.get("DREAMWEAVE_LLM_BASE_URL") or "").rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("DREAMWEAVE_LLM_API_KEY")
        self.model = model or os.environ.get("DREAMWEAVE_LLM_MODEL")
        self.timeout = timeout
        if not self.base_url:
            raise LLMError("缺少 LLM 服务地址：请设置 DREAMWEAVE_LLM_BASE_URL")
        if not self.model:
            raise LLMError("缺少 LLM 模型名：请设置 DREAMWEAVE_LLM_MODEL")

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def complete(self, prompt: str) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": TEMPERATURE,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"LLM 请求失败：{exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM 响应不是 JSON：{exc}") from exc
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM 响应缺少 choices[0].message.content") from exc


def _repair_prompt(prompt: str, errors: list[dict[str, str]]) -> str:
    lines = [
        prompt,
        "",
        "【上一次输出被拒绝，请修正以下问题后重新输出完整 JSON】",
    ]
    lines += [f"- {item.get('code')} @ {item.get('path')}：{item.get('message')}" for item in errors]
    return "\n".join(lines)


def generate_story(
    session: WorldSession,
    *,
    client: ChatClient | None = None,
    max_repairs: int = 1,
    dry_run: bool = False,
    nation_id: str | None = None,
    class_id: str | None = None,
    npc_id: str | None = None,
    scene_id: str | None = None,
    day: int = 1,
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """生成、校验并在需要时带错误反馈重试一次；只有通过校验才写盘。"""
    built = build_story_context(
        session,
        nation_id=nation_id,
        class_id=class_id,
        npc_id=npc_id,
        scene_id=scene_id,
        day=day,
        actions=actions,
    )
    if client is None:
        client = HttpChatClient()

    base_prompt = built["prompt"]
    prompt = base_prompt
    attempts = 0
    last: dict[str, Any] | None = None
    errors: list[dict[str, str]] = []
    repairs = max(0, max_repairs)

    for attempt in range(1, repairs + 2):
        attempts = attempt
        try:
            payload = extract_json(client.complete(prompt))
        except LLMError as exc:
            last = None
            errors = [{"code": "LLM_ERROR", "path": "/", "message": str(exc)}]
        else:
            if isinstance(payload, dict) and payload.get("story_key") != built["story_key"]:
                last = None
                errors = [
                    {
                        "code": "OUTLINE_STORY_KEY",
                        "path": "/story_key",
                        "message": f"story_key 必须为 {built['story_key']}，实际为 {payload.get('story_key')}",
                    }
                ]
            else:
                quality_errors = outline_quality_errors(payload, built["context"])
                if quality_errors:
                    last = None
                    errors = quality_errors
                else:
                    last = handle_story_outline(session, payload, dry_run=dry_run)
                    if last["accepted"]:
                        return {
                            "accepted": True,
                            "attempts": attempts,
                            "story_key": built["story_key"],
                            "title": payload.get("title"),
                            "logline": payload.get("logline"),
                            "result": last,
                            "errors": [],
                        }
                    errors = last["errors"]
        if attempt <= repairs:
            prompt = _repair_prompt(base_prompt, errors)

    return {
        "accepted": False,
        "attempts": attempts,
        "story_key": built["story_key"],
        "title": None,
        "logline": None,
        "message": "AI 剧情未通过校验",
        "result": last,
        "errors": errors,
    }
