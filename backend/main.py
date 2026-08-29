from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from browser_agent import BrowserAgent
from config import ENV_FILE, get_settings
from llm_router import LLMRouter
from models import (
    KeyUpdate,
    ManualClick,
    ManualKey,
    ManualType,
    PriorityUpdate,
    RunRequest,
)

app = FastAPI(title="Chrome Agent", version="1.0.0")
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_origin,
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

runs: dict[str, dict[str, Any]] = {}
subscribers: dict[str, set[WebSocket]] = {}


def router(preferred: str = "auto") -> LLMRouter:
    return LLMRouter(get_settings(), preferred)


async def broadcast(run_id: str, event: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for ws in subscribers.get(run_id, set()):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)

    for ws in dead:
        subscribers.get(run_id, set()).discard(ws)


async def persist(run_id: str) -> None:
    item = runs[run_id]
    data = {
        k: v
        for k, v in item.items()
        if k not in {"agent", "task_handle"}
    }
    path = get_settings().logs_dir / f"{run_id}.json"
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        "utf-8",
    )


async def execute_run(run_id: str) -> None:
    item = runs[run_id]
    agent: BrowserAgent = item["agent"]

    try:
        result = await agent.run()
        item.update(result)
    except Exception as exc:
        item.update(
            status="failed",
            result=f"{type(exc).__name__}: {exc}",
        )

    item["finished_at"] = datetime.now(timezone.utc).isoformat()
    await persist(run_id)
    await broadcast(
        run_id,
        {
            "type": "status",
            "run_id": run_id,
            "status": item["status"],
            "message": item.get("result", ""),
        },
    )


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/runs")
async def start_run(req: RunRequest):
    run_id = uuid.uuid4().hex[:12]
    item: dict[str, Any] = {
        "id": run_id,
        "task": req.task,
        "status": "starting",
        "provider": req.provider,
        "headless": req.headless,
        "max_steps": req.max_steps,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "steps": [],
        "result": "",
        "duration": 0,
    }

    async def sink(event: dict[str, Any]):
        if event.get("type") == "step":
            item["steps"] = item.get("steps", []) + [event]

        if event.get("type") == "status":
            item["status"] = event.get(
                "status",
                item["status"],
            )

        await broadcast(run_id, event)

    agent = BrowserAgent(
        run_id,
        req,
        get_settings(),
        router(req.provider),
        sink,
    )

    item["agent"] = agent
    runs[run_id] = item
    item["task_handle"] = asyncio.create_task(
        execute_run(run_id)
    )

    return {"id": run_id, "status": "starting"}


@app.websocket("/ws/runs/{run_id}")
async def run_ws(websocket: WebSocket, run_id: str):
    path = get_settings().logs_dir / f"{run_id}.json"
    if run_id not in runs and not path.exists():
        await websocket.close(code=4404)
        return

    await websocket.accept()
    subscribers.setdefault(run_id, set()).add(websocket)

    try:
        if run_id in runs:
            item = runs[run_id]
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "run_id": run_id,
                    "status": item.get("status"),
                    "steps": item.get("steps", []),
                }
            )

        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        pass

    finally:
        subscribers.get(run_id, set()).discard(websocket)


def active_agent(run_id: str) -> BrowserAgent:
    item = runs.get(run_id)
    if not item or "agent" not in item:
        raise HTTPException(
            status_code=404,
            detail="Active run not found",
        )
    return item["agent"]


@app.post("/api/runs/{run_id}/pause")
async def pause_run(run_id: str):
    await active_agent(run_id).pause("Paused by user")
    return {"ok": True}


@app.post("/api/runs/{run_id}/takeover")
async def takeover(run_id: str):
    await active_agent(run_id).enable_takeover()
    return {"ok": True}


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str):
    await active_agent(run_id).resume()
    return {"ok": True}


@app.post("/api/runs/{run_id}/stop")
async def stop_run(run_id: str):
    await active_agent(run_id).stop()
    return {"ok": True}


@app.post("/api/runs/{run_id}/manual/click")
async def manual_click(
    run_id: str,
    body: ManualClick,
):
    await active_agent(run_id).manual_click(
        body.x,
        body.y,
    )
    return {"ok": True}


@app.post("/api/runs/{run_id}/manual/type")
async def manual_type(
    run_id: str,
    body: ManualType,
):
    await active_agent(run_id).manual_type(
        body.text
    )
    return {"ok": True}


@app.post("/api/runs/{run_id}/manual/key")
async def manual_key(
    run_id: str,
    body: ManualKey,
):
    await active_agent(run_id).manual_key(
        body.key
    )
    return {"ok": True}


@app.get("/api/history")
async def history():
    items = []

    for path in sorted(
        get_settings().logs_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(
                path.read_text("utf-8")
            )
            items.append(
                {
                    k: data.get(k)
                    for k in [
                        "id",
                        "task",
                        "status",
                        "duration",
                        "provider",
                        "created_at",
                        "finished_at",
                    ]
                }
                | {
                    "step_count": len(
                        data.get("steps", [])
                    )
                }
            )
        except Exception:
            continue

    for run_id, data in runs.items():
        if not any(
            x.get("id") == run_id
            for x in items
        ):
            items.insert(
                0,
                {
                    "id": run_id,
                    "task": data["task"],
                    "status": data["status"],
                    "duration": data.get(
                        "duration",
                        0,
                    ),
                    "provider": data["provider"],
                    "created_at": data[
                        "created_at"
                    ],
                    "step_count": len(
                        data.get("steps", [])
                    ),
                },
            )

    return items[:100]


@app.get("/api/history/{run_id}")
async def history_detail(run_id: str):
    if run_id in runs:
        return {
            k: v
            for k, v in runs[run_id].items()
            if k not in {
                "agent",
                "task_handle",
            }
        }

    path = (
        get_settings().logs_dir
        / f"{run_id}.json"
    )
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Run not found",
        )

    return json.loads(
        path.read_text("utf-8")
    )


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return (
        value[:4]
        + "••••••••"
        + value[-4:]
    )


@app.get("/api/settings")
async def get_public_settings():
    current = get_settings()
    usage = router().usage.snapshot()

    return {
        "keys": {
            "GROQ_API_KEY_1": mask(
                current.groq_api_key_1
            ),
            "GROQ_API_KEY_2": mask(
                current.groq_api_key_2
            ),
            "GROQ_API_KEY_3": mask(
                current.groq_api_key_3
            ),
            "GROQ_API_KEY_4": mask(
                current.groq_api_key_4
            ),
            "GROQ_API_KEY_5": mask(
                current.groq_api_key_5
            ),
            "GEMINI_API_KEY": mask(
                current.gemini_api_key
            ),
        },
        "provider_priority": current.priorities,
        "models": {
            "groq": current.groq_model,
            "gemini": current.gemini_model,
            "ollama": current.ollama_model,
        },
        "usage": usage,
    }


def update_env(name: str, value: str) -> None:
    lines = (
        ENV_FILE.read_text(
            "utf-8"
        ).splitlines()
        if ENV_FILE.exists()
        else []
    )

    pattern = re.compile(
        rf"^{re.escape(name)}="
    )
    replacement = f"{name}={value}"

    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = replacement
            break
    else:
        lines.append(replacement)

    ENV_FILE.write_text(
        "\n".join(lines) + "\n",
        "utf-8",
    )


@app.post("/api/settings/key")
async def set_key(body: KeyUpdate):
    update_env(
        body.name,
        body.value.strip(),
    )
    return {"ok": True}


@app.delete("/api/settings/key/{name}")
async def delete_key(name: str):
    allowed = {
        "GROQ_API_KEY_1",
        "GROQ_API_KEY_2",
        "GROQ_API_KEY_3",
        "GROQ_API_KEY_4",
        "GROQ_API_KEY_5",
        "GEMINI_API_KEY",
    }

    if name not in allowed:
        raise HTTPException(
            status_code=400,
            detail="Invalid key name",
        )

    update_env(name, "")
    return {"ok": True}


@app.post("/api/settings/test/{provider}")
async def test_provider(provider: str):
    if provider not in {
        "groq",
        "gemini",
        "ollama",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid provider",
        )

    try:
        return await router().test_provider(
            provider
        )
    except Exception as exc:
        return {
            "ok": False,
            "provider": provider,
            "error": str(exc),
        }


@app.post("/api/settings/priority")
async def set_priority(
    body: PriorityUpdate,
):
    if (
        len(set(body.providers))
        != len(body.providers)
        or not body.providers
    ):
        raise HTTPException(
            status_code=400,
            detail="Priority list must contain unique providers",
        )

    update_env(
        "PROVIDER_PRIORITY",
        ",".join(body.providers),
    )
    return {"ok": True}
