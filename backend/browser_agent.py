from __future__ import annotations

import asyncio
import random
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from browser_use import Agent, Browser
from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import Playwright, async_playwright

from config import Settings
from llm_router import LLMRouter
from models import RunRequest

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


class BrowserAgent:
    """Browser Use Agent attached to a Chromium instance launched by Playwright."""

    def __init__(
        self,
        run_id: str,
        request: RunRequest,
        settings: Settings,
        router: LLMRouter,
        sink: EventSink,
    ):
        self.run_id = run_id
        self.request = request
        self.settings = settings
        self.router = router
        self.sink = sink

        self.agent: Agent | None = None
        self.browser: Browser | None = None
        self.playwright: Playwright | None = None
        self.playwright_browser: PlaywrightBrowser | None = None
        self.screenshot_task: asyncio.Task | None = None

        self.takeover = False
        self.stop_requested = False
        self.started = time.monotonic()
        self.step_number = 0
        self.dom_failures = 0
        self.steps: list[dict[str, Any]] = []
        self._safety_pause_reason: str | None = None

    async def emit(self, kind: str, **payload: Any) -> None:
        await self.sink(
            {
                "type": kind,
                "run_id": self.run_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                **payload,
            }
        )

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    async def _wait_for_cdp(self, port: int) -> str:
        url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(timeout=1.5) as client:
            for _ in range(30):
                try:
                    response = await client.get(f"{url}/json/version")
                    if response.is_success:
                        return url
                except Exception:
                    pass
                await asyncio.sleep(0.15)
        raise RuntimeError("Chromium CDP endpoint did not become ready")

    async def _launch_browser(self) -> Browser:
        """Launch Chromium with Playwright, then attach Browser Use through CDP."""
        port = self._free_port()
        self.playwright = await async_playwright().start()
        self.playwright_browser = await self.playwright.chromium.launch(
            headless=self.request.headless,
            args=[
                f"--remote-debugging-port={port}",
                "--remote-allow-origins=*",
            ],
        )
        cdp_url = await self._wait_for_cdp(port)

        return Browser(
            cdp_url=cdp_url,
            keep_alive=True,
            viewport={
                "width": self.settings.viewport_width,
                "height": self.settings.viewport_height,
            },
            window_size={
                "width": self.settings.viewport_width,
                "height": self.settings.viewport_height,
            },
            highlight_elements=True,
            cross_origin_iframes=True,
        )

    async def pause(self, reason: str = "Paused") -> None:
        if self.agent:
            self.agent.pause()
        await self.emit(
            "status",
            status="paused",
            message=reason,
            takeover=self.takeover,
        )

    async def resume(self) -> None:
        self.takeover = False
        self._safety_pause_reason = None
        if self.agent:
            self.agent.resume()
        await self.emit(
            "status",
            status="running",
            message="Agent resumed",
            takeover=False,
        )

    async def enable_takeover(self) -> None:
        self.takeover = True
        if self.agent:
            self.agent.pause()
        await self.emit(
            "status",
            status="paused",
            message="Manual takeover active",
            takeover=True,
        )

    async def stop(self) -> None:
        self.stop_requested = True
        if self.agent:
            self.agent.stop()
        await self.emit(
            "status",
            status="stopping",
            message="Stopping browser",
        )

    async def _cdp_session(self):
        if not self.browser or not self.browser.agent_focus_target_id:
            raise RuntimeError("No focused browser target")

        return await self.browser.get_or_create_cdp_session(
            self.browser.agent_focus_target_id,
            focus=False,
        )

    async def manual_click(self, x: float, y: float) -> None:
        if not self.takeover or not self.browser:
            raise RuntimeError("Take Over mode is not active")

        session = await self._cdp_session()
        for event_type in ("mousePressed", "mouseReleased"):
            await self.browser.cdp_client.send.Input.dispatchMouseEvent(
                params={
                    "type": event_type,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
                session_id=session.session_id,
            )

    async def manual_type(self, text: str) -> None:
        if not self.takeover or not self.browser:
            raise RuntimeError("Take Over mode is not active")

        session = await self._cdp_session()
        for char in text:
            await self.browser.cdp_client.send.Input.dispatchKeyEvent(
                params={"type": "char", "text": char},
                session_id=session.session_id,
            )

    async def manual_key(self, key: str) -> None:
        if not self.takeover or not self.browser:
            raise RuntimeError("Take Over mode is not active")

        session = await self._cdp_session()
        for event_type in ("keyDown", "keyUp"):
            await self.browser.cdp_client.send.Input.dispatchKeyEvent(
                params={"type": event_type, "key": key},
                session_id=session.session_id,
            )

    async def _screenshots(self) -> None:
        while not self.stop_requested:
            try:
                if self.browser:
                    state = await self.browser.get_browser_state_summary()
                    if state.screenshot:
                        width = self.settings.viewport_width
                        height = self.settings.viewport_height

                        if state.page_info:
                            width = state.page_info.viewport_width
                            height = state.page_info.viewport_height

                        await self.emit(
                            "screenshot",
                            image=f"data:image/png;base64,{state.screenshot}",
                            width=width,
                            height=height,
                            url=state.url,
                        )
            except Exception:
                pass

            await asyncio.sleep(self.settings.screenshot_interval_seconds)

    @staticmethod
    def _state_text(state: Any) -> str:
        dom = getattr(state, "dom_state", None)
        if dom is None:
            return ""

        for method_name in ("llm_representation", "to_string"):
            method = getattr(dom, method_name, None)
            if callable(method):
                try:
                    value = method()
                    if isinstance(value, str):
                        return value
                except Exception:
                    pass

        return str(dom)

    async def _safety_check(self, agent: Agent) -> None:
        if self.takeover or self._safety_pause_reason:
            return

        try:
            state = await agent.browser_session.get_browser_state_summary()
        except Exception:
            return

        haystack = (
            f"{state.title}\n{state.url}\n{self._state_text(state)}"
        ).lower()

        captcha_or_auth = [
            "captcha",
            "verify you are human",
            "i'm not a robot",
            "security challenge",
            "two-factor",
            "2fa",
            "verification code",
            "one-time password",
            "enter otp",
            "sign in to continue",
            "login to continue",
            "log in to continue",
        ]
        payment = [
            "card number",
            "cvv",
            "place order",
            "complete purchase",
            "pay now",
            "checkout",
        ]

        if any(term in haystack for term in captcha_or_auth):
            self._safety_pause_reason = "authentication_or_captcha"
            agent.pause()
            await self.emit(
                "status",
                status="paused",
                message="User action required: CAPTCHA, login, or verification detected",
                takeover=False,
            )
            await self.emit(
                "needs_user_action",
                reason="authentication_or_captcha",
            )

        elif any(term in haystack for term in payment):
            self._safety_pause_reason = "payment_confirmation"
            agent.pause()
            await self.emit(
                "status",
                status="paused",
                message="Payment/checkout detected. Confirm manually before resuming",
                takeover=False,
            )
            await self.emit(
                "needs_user_action",
                reason="payment_confirmation",
            )

    async def _on_step_start(self, agent: Agent) -> None:
        try:
            state = await agent.browser_session.get_browser_state_summary()
            selector_map = getattr(state.dom_state, "selector_map", {})
            if selector_map:
                self.dom_failures = 0
                self.router.vision_required = False
            else:
                self.dom_failures += 1

            if self.dom_failures >= 2:
                self.router.vision_required = True
                agent.settings.use_vision = True
                await self.emit(
                    "status",
                    status="running",
                    message="DOM unavailable twice; vision fallback enabled",
                )
        except Exception:
            self.dom_failures += 1

        await self._safety_check(agent)

    async def _on_step_end(self, agent: Agent) -> None:
        self.step_number += 1

        history = agent.history
        outputs = history.model_outputs()
        output = outputs[-1] if outputs else None
        action_names = history.action_names()
        errors = history.errors()
        extracted = history.extracted_content()

        action_name = action_names[-1] if action_names else "unknown"
        error = errors[-1] if errors else None
        success = not bool(error)
        result = extracted[-1] if extracted else ""
        reasoning = ""
        target = None
        value = None

        if output is not None:
            reasoning = (
                getattr(output, "thinking", None)
                or getattr(output, "next_goal", None)
                or getattr(output, "evaluation_previous_goal", None)
                or ""
            )

            actions = getattr(output, "action", None) or []
            if actions:
                try:
                    payload = actions[-1].model_dump(exclude_none=True)
                    if payload:
                        action_name = next(iter(payload.keys()))
                        params = payload.get(action_name)

                        if isinstance(params, dict):
                            target = (
                                params.get("index")
                                or params.get("url")
                                or params.get("text")
                            )
                            value = (
                                params.get("text")
                                or params.get("keys")
                            )
                except Exception:
                    pass

        record = {
            "step": self.step_number,
            "action": action_name,
            "target": target,
            "value": value,
            "provider": self.router.last_provider,
            "success": success,
            "reasoning": str(reasoning),
            "result": str(error or result or "ok")[:2000],
        }

        self.steps.append(record)
        await self.emit("step", **record)

        await asyncio.sleep(
            random.uniform(
                self.settings.human_delay_min_ms,
                self.settings.human_delay_max_ms,
            )
            / 1000
        )

    async def run(self) -> dict[str, Any]:
        status = "failed"
        final_result = ""

        try:
            self.browser = await self._launch_browser()
            uploads = Path(__file__).resolve().parent / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)

            self.agent = Agent(
                task=self.request.task,
                llm=self.router,
                browser=self.browser,
                use_vision="auto",
                max_failures=2,
                max_actions_per_step=1,
                step_timeout=self.settings.step_timeout_seconds,
                llm_timeout=self.settings.step_timeout_seconds,
                available_file_paths=[str(uploads)],
                extend_system_message=(
                    "Pause rather than trying to bypass CAPTCHA, OTP/2FA, login walls, "
                    "or payment confirmation. Prefer DOM/index actions. Use screenshots "
                    "or coordinate interaction only when DOM interaction is insufficient."
                ),
            )

            self.screenshot_task = asyncio.create_task(self._screenshots())
            await self.emit(
                "status",
                status="running",
                message="Browser Use agent attached to Playwright Chromium",
            )

            history = await self.agent.run(
                max_steps=self.request.max_steps,
                on_step_start=self._on_step_start,
                on_step_end=self._on_step_end,
            )

            if self.stop_requested:
                status = "stopped"
                final_result = "Stopped by user"

            elif history.is_done() and history.is_successful() is not False:
                status = "completed"
                final_result = history.final_result() or "Task completed"

            else:
                status = "failed"
                final_result = (
                    history.final_result()
                    or "Agent stopped before reporting successful completion"
                )

            return {
                "status": status,
                "result": final_result,
                "steps": self.steps,
                "duration": round(time.monotonic() - self.started, 2),
            }

        finally:
            self.stop_requested = True

            if self.screenshot_task:
                self.screenshot_task.cancel()

            if self.agent:
                self.agent.stop()

            if self.browser:
                try:
                    await self.browser.kill()
                except Exception:
                    pass

            if self.playwright_browser:
                try:
                    await self.playwright_browser.close()
                except Exception:
                    pass

            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
