"""Tier 1: local open-source models served by Ollama (https://github.com/ollama/ollama)."""

from __future__ import annotations

import base64
import json
from typing import Any, Iterator

import httpx


class LocalUnavailable(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/api/tags", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def models(self) -> list[dict[str, Any]]:
        try:
            return httpx.get(f"{self.base_url}/api/tags", timeout=5).json().get("models", [])
        except httpx.HTTPError as e:
            raise LocalUnavailable(str(e)) from e

    def chat(self, model: str, messages: list[dict[str, Any]], *, schema: dict[str, Any] | None = None,
             images: list[bytes] | None = None, temperature: float = 0.0, num_ctx: int = 16384) -> dict[str, Any]:
        msgs = [dict(m) for m in messages]
        if images:
            msgs[-1]["images"] = [base64.b64encode(i).decode() for i in images]
        body: dict[str, Any] = {"model": model, "messages": msgs, "stream": False, "think": False,
                                "options": {"temperature": temperature, "num_ctx": num_ctx}}
        if schema:
            body["format"] = schema
        try:
            r = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
            if r.status_code == 400 and "think" in r.text:
                body.pop("think")  # models without a thinking mode reject the flag
                r = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LocalUnavailable(f"ollama chat failed: {e}") from e
        data = r.json()
        return {"text": data["message"]["content"], "input_tokens": data.get("prompt_eval_count"),
                "output_tokens": data.get("eval_count"), "seconds": (data.get("total_duration") or 0) / 1e9}

    def chat_json(self, model: str, messages: list[dict[str, Any]], schema: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        out = self.chat(model, messages, schema=schema, **kw)
        try:
            return json.loads(out["text"]), out
        except json.JSONDecodeError as e:
            raise LocalUnavailable(f"model returned invalid JSON: {e}") from e

    def stream(self, model: str, messages: list[dict[str, Any]], temperature: float = 0.1, num_ctx: int = 16384) -> Iterator[str]:
        body = {"model": model, "messages": messages, "stream": True, "think": False,
                "options": {"temperature": temperature, "num_ctx": num_ctx}}
        try:
            with httpx.stream("POST", f"{self.base_url}/api/chat", json=body, timeout=self.timeout) as r:
                if r.status_code == 400:
                    raise LocalUnavailable(r.read().decode()[:200])
                for line in r.iter_lines():
                    if line:
                        chunk = json.loads(line)
                        if chunk.get("message", {}).get("content"):
                            yield chunk["message"]["content"]
        except httpx.HTTPError as e:
            raise LocalUnavailable(f"ollama stream failed: {e}") from e

    def pull(self, model: str) -> None:
        with httpx.stream("POST", f"{self.base_url}/api/pull", json={"model": model}, timeout=None) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line and json.loads(line).get("error"):
                    raise LocalUnavailable(json.loads(line)["error"])
