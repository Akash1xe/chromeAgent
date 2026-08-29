from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI, RateLimitError

from config import Settings
from models import AgentAction

SYSTEM_PROMPT = """You are the decision engine for a browser automation agent.
Return ONE JSON object only. No markdown.
Schema:
{
  "action": "goto|click|type|select|check|uncheck|scroll|hover|drag|upload_file|key_press|new_tab|switch_tab|close_tab|extract_text|wait_for_element|switch_iframe|coordinate_click|done",
  "target": "stable element id, selector, URL, direction, iframe id, or null",
  "value": "text/option/key/file path/wait condition or null",
  "x": null,
  "y": null,
  "tab_index": null,
  "reasoning": "brief operational reason",
  "result": "final answer only when action is done"
}
Use DOM ids whenever possible. Do not invent elements. Prefer deterministic DOM actions over coordinates.
When the task is complete, return done. Never submit payments or bypass CAPTCHA/2FA.
"""


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
        self.path.write_text(json.dumps({"day": self.day, "daily": self.daily}, indent=2), "utf-8")

    def snapshot(self) -> dict[str, dict[str, int]]:
        now = time.time()
        out = {}
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
    def __init__(self, settings: Settings):
        self.settings = settings
        self.usage = UsageTracker(settings.usage_file)
        self._groq_cursor = 0

    def _parse_action(self, text: str) -> AgentAction:
        cleaned = text.strip()
        if cleaned.startswith("~~~"):
            cleaned = cleaned.strip("~").removeprefix("json").strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError(f"LLM returned non-JSON content: {cleaned[:300]}")
        return AgentAction.model_validate(json.loads(cleaned[start:end + 1]))

    async def decide(
        self,
        prompt: str,
        preferred: str = "auto",
        screenshot: bytes | None = None,
    ) -> tuple[AgentAction, str]:
        order = (
            self.settings.priorities
            if preferred == "auto"
            else [preferred] + [p for p in self.settings.priorities if p != preferred]
        )
        errors: list[str] = []
        for provider in order:
            try:
                if provider == "groq":
                    return await self._groq(prompt), "groq"
                if provider == "gemini":
                    return await self._gemini(prompt, screenshot), "gemini"
                if provider == "ollama":
                    return await self._ollama(prompt), "ollama"
            except Exception as exc:
                errors.append(f"{provider}: {type(exc).__name__}: {exc}")
        raise RuntimeError("All LLM providers failed. " + " | ".join(errors))

    async def _groq(self, prompt: str) -> AgentAction:
        keys = self.settings.groq_keys
        if not keys:
            raise RuntimeError("No Groq keys configured")

        last_error: Exception | None = None
        for offset in range(len(keys)):
            idx = (self._groq_cursor + offset) % len(keys)
            key = keys[idx]
            key_id = f"groq_{idx + 1}"
            try:
                client = AsyncOpenAI(
                    api_key=key,
                    base_url="https://api.groq.com/openai/v1",
                    max_retries=0,
                )
                self.usage.record(key_id)
                response = await client.chat.completions.create(
                    model=self.settings.groq_model,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                self._groq_cursor = idx
                return self._parse_action(response.choices[0].message.content or "")
            except RateLimitError as exc:
                last_error = exc
                self._groq_cursor = (idx + 1) % len(keys)
                await asyncio.sleep(0.25 + 0.15 * offset)

        raise last_error or RuntimeError("All Groq keys unavailable")

    async def _gemini(self, prompt: str, screenshot: bytes | None = None) -> AgentAction:
        if not self.settings.gemini_api_key:
            raise RuntimeError("No Gemini key configured")

        self.usage.record("gemini")
        parts: list[dict[str, Any]] = [{"text": SYSTEM_PROMPT + "\n\n" + prompt}]
        if screenshot:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(screenshot).decode(),
                    }
                }
            )

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.settings.gemini_model}:generateContent"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                params={"key": self.settings.gemini_api_key},
                json={
                    "contents": [{"parts": parts}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                    },
                },
            )
            if response.status_code == 429:
                raise RuntimeError("Gemini rate limited")
            response.raise_for_status()
            data = response.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return self._parse_action(text)

    async def _ollama(self, prompt: str) -> AgentAction:
        self.usage.record("ollama")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                json={
                    "model": self.settings.ollama_model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"temperature": 0.1},
                },
            )
            response.raise_for_status()
            data = response.json()
        return self._parse_action(data["message"]["content"])

    async def test_provider(self, provider: str) -> dict[str, Any]:
        action, used = await self.decide(
            "Return action done with result 'ok'.",
            provider,
        )
        return {"ok": action.action == "done", "provider": used}
