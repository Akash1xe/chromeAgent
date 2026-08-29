from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

import httpx
from openai import AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from browser_use import ChatGoogle, ChatOllama
from browser_use.llm.groq.serializer import GroqMessageSerializer
from browser_use.llm.messages import BaseMessage
from browser_use.llm.views import ChatInvokeCompletion

from config import Settings

T = TypeVar("T", bound=BaseModel)


class UsageTracker:
    def __init__(self, path: Path):
        self.path = path
        self.minute_calls: dict[str, deque[float]] = defaultdict(deque)
        self.day = date.today().isoformat()
        self.daily: dict[str, int] = defaultdict(int)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if data.get("day") == self.day:
                self.daily.update(data.get("daily", {}))
                now = time.time()
                for key_id, timestamps in data.get("recent", {}).items():
                    self.minute_calls[key_id].extend(
                        float(ts)
                        for ts in timestamps
                        if now - float(ts) <= 60
                    )
        except Exception:
            pass

    def record(self, key_id: str) -> None:
        now = time.time()
        q = self.minute_calls[key_id]
        q.append(now)
        while q and now - q[0] > 60:
            q.popleft()
        self.daily[key_id] += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "day": self.day,
                    "daily": dict(self.daily),
                    "recent": {
                        key: list(values)
                        for key, values in self.minute_calls.items()
                    },
                },
                indent=2,
            ),
            "utf-8",
        )

    def snapshot(self) -> dict[str, dict[str, int]]:
        now = time.time()
        out: dict[str, dict[str, int]] = {}
        for key_id in set(self.daily) | set(self.minute_calls):
            q = self.minute_calls[key_id]
            while q and now - q[0] > 60:
                q.popleft()
            out[key_id] = {
                "requests_today": self.daily[key_id],
                "requests_last_minute": len(q),
            }
        return out


class LLMRouter:
    """Browser Use-compatible chat model with provider/key fallback."""

    _verified_api_keys = True

    def __init__(self, settings: Settings, preferred: str = "auto"):
        self.settings = settings
        self.preferred = preferred
        self.usage = UsageTracker(settings.usage_file)
        self._groq_cursor = 0
        self.last_provider = "none"
        self.vision_required = False
        self.model = "chrome-agent-router"

    @property
    def provider(self) -> str:
        return "chrome-agent-router"

    @property
    def name(self) -> str:
        return self.model

    def _order(self) -> list[str]:
        if self.vision_required and self.settings.gemini_api_key:
            return ["gemini"] + [
                p for p in self.settings.priorities if p != "gemini"
            ]
        if self.preferred == "auto":
            return self.settings.priorities
        return [self.preferred] + [
            p for p in self.settings.priorities if p != self.preferred
        ]

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        errors: list[str] = []
        for provider in self._order():
            try:
                if provider == "groq":
                    result = await self._groq(messages, output_format)
                elif provider == "gemini":
                    result = await self._gemini(messages, output_format)
                elif provider == "ollama":
                    result = await self._ollama(messages, output_format)
                else:
                    continue
                self.last_provider = provider
                return result
            except Exception as exc:
                errors.append(f"{provider}: {type(exc).__name__}: {exc}")

        raise RuntimeError("All LLM providers failed. " + " | ".join(errors))

    async def _groq(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        keys = self.settings.groq_keys
        if not keys:
            raise RuntimeError("No Groq keys configured")

        groq_messages = GroqMessageSerializer.serialize_messages(messages)
        last_error: Exception | None = None

        for offset in range(len(keys)):
            idx = (self._groq_cursor + offset) % len(keys)
            key_id = f"groq_{idx + 1}"
            self.usage.record(key_id)

            try:
                client = AsyncOpenAI(
                    api_key=keys[idx],
                    base_url="https://api.groq.com/openai/v1",
                    max_retries=0,
                )
                request_kwargs: dict[str, Any] = {
                    "model": self.settings.groq_model,
                    "messages": groq_messages,
                    "temperature": 0.1,
                }
                if output_format is not None:
                    request_kwargs["response_format"] = {"type": "json_object"}

                response = await client.chat.completions.create(**request_kwargs)
                content = response.choices[0].message.content or ""
                self._groq_cursor = idx

                if output_format is None:
                    return ChatInvokeCompletion(
                        completion=content,
                        usage=None,
                    )

                return ChatInvokeCompletion(
                    completion=output_format.model_validate_json(content),
                    usage=None,
                )

            except RateLimitError as exc:
                last_error = exc
                self._groq_cursor = (idx + 1) % len(keys)
                await asyncio.sleep(0.25 + 0.15 * offset)

        raise last_error or RuntimeError("All Groq keys unavailable")

    async def _gemini(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        if not self.settings.gemini_api_key:
            raise RuntimeError("No Gemini key configured")

        self.usage.record("gemini")
        model = ChatGoogle(
            model=self.settings.gemini_model,
            api_key=self.settings.gemini_api_key,
            max_retries=0,
        )
        return await model.ainvoke(messages, output_format)

    async def _ollama(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        self.usage.record("ollama")
        model = ChatOllama(
            model=self.settings.ollama_model,
            host=self.settings.ollama_base_url,
            timeout=60,
        )
        return await model.ainvoke(messages, output_format)

    async def test_key(self, name: str) -> dict[str, Any]:
        groq_map = {
            "GROQ_API_KEY_1": self.settings.groq_api_key_1,
            "GROQ_API_KEY_2": self.settings.groq_api_key_2,
            "GROQ_API_KEY_3": self.settings.groq_api_key_3,
            "GROQ_API_KEY_4": self.settings.groq_api_key_4,
            "GROQ_API_KEY_5": self.settings.groq_api_key_5,
        }

        if name in groq_map:
            key = groq_map[name]
            if not key:
                raise RuntimeError(f"{name} is not configured")
            client = AsyncOpenAI(
                api_key=key,
                base_url="https://api.groq.com/openai/v1",
                max_retries=0,
            )
            await client.chat.completions.create(
                model=self.settings.groq_model,
                messages=[{"role": "user", "content": "Reply with ok"}],
                max_tokens=4,
            )
            return {"ok": True, "key": name, "provider": "groq"}

        if name == "GEMINI_API_KEY":
            if not self.settings.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY is not configured")
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.settings.gemini_model}:generateContent"
            )
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    url,
                    params={"key": self.settings.gemini_api_key},
                    json={
                        "contents": [
                            {"parts": [{"text": "Reply with ok"}]}
                        ]
                    },
                )
                response.raise_for_status()
            return {"ok": True, "key": name, "provider": "gemini"}

        raise RuntimeError(f"Unknown key: {name}")

    async def test_provider(self, provider: str) -> dict[str, Any]:
        if provider == "groq":
            if not self.settings.groq_keys:
                raise RuntimeError("No Groq key configured")
            client = AsyncOpenAI(
                api_key=self.settings.groq_keys[0],
                base_url="https://api.groq.com/openai/v1",
                max_retries=0,
            )
            await client.chat.completions.create(
                model=self.settings.groq_model,
                messages=[{"role": "user", "content": "Reply with ok"}],
                max_tokens=4,
            )
            return {"ok": True, "provider": "groq"}

        if provider == "gemini":
            if not self.settings.gemini_api_key:
                raise RuntimeError("No Gemini key configured")
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.settings.gemini_model}:generateContent"
            )
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    url,
                    params={"key": self.settings.gemini_api_key},
                    json={
                        "contents": [
                            {"parts": [{"text": "Reply with ok"}]}
                        ]
                    },
                )
                response.raise_for_status()
            return {"ok": True, "provider": "gemini"}

        if provider == "ollama":
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"{self.settings.ollama_base_url.rstrip('/')}/api/tags"
                )
                response.raise_for_status()
            return {"ok": True, "provider": "ollama"}

        raise RuntimeError(f"Unknown provider: {provider}")
