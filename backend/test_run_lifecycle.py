import json

import pytest

import main
from config import Settings
from models import RunRequest


class FakeBrowserAgent:
    def __init__(self, run_id, request, settings, router, sink):
        self.run_id = run_id
        self.request = request
        self.sink = sink

    async def run(self):
        await self.sink(
            {
                "type": "status",
                "run_id": self.run_id,
                "status": "running",
                "message": "Fake agent started",
            }
        )
        step = {
            "type": "step",
            "run_id": self.run_id,
            "step": 1,
            "action": "goto",
            "target": "https://example.com",
            "value": None,
            "provider": "groq",
            "success": True,
            "reasoning": "Open the requested page",
            "result": "ok",
        }
        await self.sink(step)
        return {
            "status": "completed",
            "result": "Task completed",
            "steps": [step],
            "duration": 0.01,
        }


@pytest.mark.asyncio
async def test_run_lifecycle_streams_and_persists(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        logs_dir=tmp_path / "logs",
        usage_file=tmp_path / "usage.json",
    )
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    events = []

    async def capture_broadcast(run_id, event):
        events.append(event)

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "BrowserAgent", FakeBrowserAgent)
    monkeypatch.setattr(main, "router", lambda preferred="auto": object())
    monkeypatch.setattr(main, "broadcast", capture_broadcast)

    main.runs.clear()
    main.subscribers.clear()

    response = await main.start_run(
        RunRequest(
            task="Open example.com",
            provider="auto",
            headless=True,
            max_steps=5,
        )
    )

    run_id = response["id"]
    assert response["status"] == "starting"
    assert run_id in main.runs

    await main.runs[run_id]["task_handle"]

    item = main.runs[run_id]
    assert item["status"] == "completed"
    assert item["result"] == "Task completed"
    assert item["duration"] == 0.01
    assert len(item["steps"]) == 1
    assert item["steps"][0]["action"] == "goto"
    assert item["steps"][0]["provider"] == "groq"

    status_events = [event for event in events if event["type"] == "status"]
    step_events = [event for event in events if event["type"] == "step"]

    assert any(event["status"] == "running" for event in status_events)
    assert any(event["status"] == "completed" for event in status_events)
    assert len(step_events) == 1
    assert step_events[0]["success"] is True

    log_path = settings.logs_dir / f"{run_id}.json"
    assert log_path.exists()

    saved = json.loads(log_path.read_text("utf-8"))
    assert saved["id"] == run_id
    assert saved["task"] == "Open example.com"
    assert saved["status"] == "completed"
    assert saved["result"] == "Task completed"
    assert saved["steps"][0]["action"] == "goto"
    assert "agent" not in saved
    assert "task_handle" not in saved

    detail = await main.history_detail(run_id)
    assert detail["status"] == "completed"
    assert detail["steps"][0]["provider"] == "groq"


@pytest.mark.asyncio
async def test_run_failure_is_persisted(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        logs_dir=tmp_path / "logs",
        usage_file=tmp_path / "usage.json",
    )
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    class FailingAgent(FakeBrowserAgent):
        async def run(self):
            raise RuntimeError("simulated failure")

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "BrowserAgent", FailingAgent)
    monkeypatch.setattr(main, "router", lambda preferred="auto": object())

    main.runs.clear()
    main.subscribers.clear()

    response = await main.start_run(RunRequest(task="Fail safely"))
    run_id = response["id"]
    await main.runs[run_id]["task_handle"]

    saved = json.loads(
        (settings.logs_dir / f"{run_id}.json").read_text("utf-8")
    )

    assert saved["status"] == "failed"
    assert "simulated failure" in saved["result"]
    assert saved["finished_at"]
