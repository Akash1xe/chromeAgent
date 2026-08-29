from pathlib import Path

import pytest

import llm_router
from config import Settings
from llm_router import LLMRouter, UsageTracker


class DummyRateLimitError(Exception):
    pass


class DummyCompletion:
    def __init__(self, content="ok"):
        self.choices = [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]


class DummyCompletions:
    def __init__(self, key, calls):
        self.key = key
        self.calls = calls

    async def create(self, **kwargs):
        self.calls.append(self.key)
        if self.key == "key-1":
            raise DummyRateLimitError("rate limited")
        return DummyCompletion("ok")


class DummyChat:
    def __init__(self, key, calls):
        self.completions = DummyCompletions(key, calls)


class DummyOpenAI:
    calls = []

    def __init__(self, api_key, **kwargs):
        self.chat = DummyChat(api_key, self.calls)


@pytest.mark.asyncio
async def test_router_falls_back_to_next_provider(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        provider_priority="groq,gemini,ollama",
        usage_file=tmp_path / "usage.json",
    )
    router = LLMRouter(settings)

    async def fail_groq(messages, output_format):
        raise RuntimeError("groq unavailable")

    async def ok_gemini(messages, output_format):
        return "gemini-ok"

    async def should_not_run(messages, output_format):
        raise AssertionError("ollama should not be called")

    monkeypatch.setattr(router, "_groq", fail_groq)
    monkeypatch.setattr(router, "_gemini", ok_gemini)
    monkeypatch.setattr(router, "_ollama", should_not_run)

    result = await router.ainvoke([])

    assert result == "gemini-ok"
    assert router.last_provider == "gemini"


def test_vision_mode_prioritizes_gemini(tmp_path):
    settings = Settings(
        _env_file=None,
        gemini_api_key="gemini-key",
        provider_priority="groq,gemini,ollama",
        usage_file=tmp_path / "usage.json",
    )
    router = LLMRouter(settings)
    router.vision_required = True

    assert router._order() == ["gemini", "groq", "ollama"]


@pytest.mark.asyncio
async def test_groq_rotates_key_after_rate_limit(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        groq_api_key_1="key-1",
        groq_api_key_2="key-2",
        usage_file=tmp_path / "usage.json",
    )
    router = LLMRouter(settings)

    DummyOpenAI.calls = []
    monkeypatch.setattr(llm_router, "AsyncOpenAI", DummyOpenAI)
    monkeypatch.setattr(llm_router, "RateLimitError", DummyRateLimitError)
    monkeypatch.setattr(
        llm_router.GroqMessageSerializer,
        "serialize_messages",
        lambda messages: [],
    )

    result = await router._groq([], None)

    assert result.completion == "ok"
    assert DummyOpenAI.calls == ["key-1", "key-2"]
    assert router._groq_cursor == 1


def test_usage_tracker_resets_when_day_changes(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    tracker = UsageTracker(path)
    tracker.record("groq_1")

    class NextDay:
        @classmethod
        def today(cls):
            return type("D", (), {"isoformat": lambda self: "2099-01-02"})()

    monkeypatch.setattr(llm_router, "date", NextDay)

    snapshot = tracker.snapshot()

    assert snapshot == {}
    assert tracker.day == "2099-01-02"
