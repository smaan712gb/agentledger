"""Tier 2: frontier reasoning via the Claude API. Used sparingly, never with raw client PII."""

from __future__ import annotations

from typing import Any, Iterator, TypeVar

import anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5-5"
# Server-side fallback: if the primary model declines, the API re-runs the request on
# Anthropic's recommended fallback for that refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class FrontierError(RuntimeError):
    pass


class ClaudeClient:
    def __init__(self, model: str = DEFAULT_MODEL, client: anthropic.Anthropic | None = None):
        self.model = model
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def available(self) -> bool:
        import os

        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or self._client)

    def structured(self, *, system: str, content: str | list[dict[str, Any]], schema: type[T], effort: str = "medium",
                   max_tokens: int = 16000) -> tuple[T, dict[str, Any]]:
        try:
            resp = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=schema,
                output_config={"effort": effort},
                thinking={"type": "adaptive"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.RateLimitError as e:
            raise FrontierError(f"rate limited: {e}") from e
        except anthropic.APIStatusError as e:
            raise FrontierError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise FrontierError(f"connection error: {e}") from e
        if resp.stop_reason == "refusal":
            raise FrontierError("model declined the request")
        if resp.stop_reason == "max_tokens":
            raise FrontierError("response truncated at max_tokens")
        if resp.parsed_output is None:
            raise FrontierError("no structured output returned")
        return resp.parsed_output, _usage(resp)

    def stream_text(self, *, system: str, messages: list[dict[str, Any]], effort: str = "high",
                    max_tokens: int = 64000) -> Iterator[str]:
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config={"effort": effort},
                thinking={"type": "adaptive"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                yield from stream.text_stream
                final = stream.get_final_message()
                if final.stop_reason == "refusal":
                    raise FrontierError("model declined the request")
        except anthropic.APIStatusError as e:
            raise FrontierError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise FrontierError(f"connection error: {e}") from e

    def web_research(self, question: str, allowed_domains: list[str], max_uses: int = 5) -> tuple[str, list[dict[str, str]]]:
        """Server-side web search restricted to official domains. Returns answer text and result URLs."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses, "allowed_domains": allowed_domains}]
        urls: list[dict[str, str]] = []
        text_parts: list[str] = []
        for _ in range(4):  # continue through pause_turn
            try:
                resp = self.client.beta.messages.create(
                    model=self.model, max_tokens=16000, messages=messages, tools=tools,
                    output_config={"effort": "medium"}, thinking={"type": "adaptive"},
                    betas=[FALLBACK_BETA], fallbacks="default",
                )
            except anthropic.APIStatusError as e:
                raise FrontierError(f"API error {e.status_code}: {e.message}") from e
            except anthropic.APIConnectionError as e:
                raise FrontierError(f"connection error: {e}") from e
            for block in resp.content:
                if block.type == "web_search_tool_result" and isinstance(block.content, list):
                    urls += [{"url": r.url, "title": r.title} for r in block.content if getattr(r, "type", "") == "web_search_result"]
                elif block.type == "text":
                    text_parts.append(block.text)
            if resp.stop_reason != "pause_turn":
                break
            messages = [*messages, {"role": "assistant", "content": resp.content}]
        seen, uniq = set(), []
        for u in urls:
            if u["url"] not in seen:
                seen.add(u["url"])
                uniq.append(u)
        return "".join(text_parts), uniq


def _usage(resp: Any) -> dict[str, Any]:
    u = getattr(resp, "usage", None)
    return {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None)}
