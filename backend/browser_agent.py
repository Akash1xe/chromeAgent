from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from config import Settings
from llm_router import LLMRouter
from models import AgentAction, RunRequest

EventSink = Callable[[dict[str, Any]], Awaitable[None]]

DOM_EXTRACTOR = r"""
() => {
  let seq = 0;
  const selector = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[contenteditable="true"],summary';
  const els = [...document.querySelectorAll(selector)].filter(el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none';
  });

  return els.slice(0, 250).map(el => {
    let id = el.getAttribute('data-agent-id');
    if (!id) {
      id = 'e' + (++seq);
      el.setAttribute('data-agent-id', id);
    }
    const r = el.getBoundingClientRect();
    return {
      id,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type'),
      role: el.getAttribute('role'),
      text: (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().slice(0, 180),
      name: el.getAttribute('name'),
      checked: !!el.checked,
      disabled: !!el.disabled,
      x: Math.round(r.x + r.width / 2),
      y: Math.round(r.y + r.height / 2)
    };
  });
}
"""


class BrowserAgent:
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

        self.pause_event = asyncio.Event()
        self.pause_event.set()
        self.stop_event = asyncio.Event()
        self.takeover = False

        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._shot_task: asyncio.Task | None = None

        self.steps: list[dict[str, Any]] = []
        self.started = time.monotonic()
        self.dom_failures = 0

    async def emit(self, kind: str, **payload: Any) -> None:
        await self.sink(
            {
                "type": kind,
                "run_id": self.run_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                **payload,
            }
        )

    async def pause(self, reason: str = "Paused") -> None:
        self.pause_event.clear()
        await self.emit("status", status="paused", message=reason, takeover=self.takeover)

    async def resume(self) -> None:
        self.takeover = False
        self.pause_event.set()
        await self.emit(
            "status",
            status="running",
            message="Agent resumed",
            takeover=False,
        )

    async def enable_takeover(self) -> None:
        self.takeover = True
        self.pause_event.clear()
        await self.emit(
            "status",
            status="paused",
            message="Manual takeover active",
            takeover=True,
        )

    async def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.set()
        await self.emit("status", status="stopping", message="Stopping browser")

    async def manual_click(self, x: float, y: float) -> None:
        if not self.page or not self.takeover:
            raise RuntimeError("Take Over mode is not active")
        await self.page.mouse.click(x, y)

    async def manual_type(self, text: str) -> None:
        if not self.page or not self.takeover:
            raise RuntimeError("Take Over mode is not active")
        await self.page.keyboard.type(text)

    async def manual_key(self, key: str) -> None:
        if not self.page or not self.takeover:
            raise RuntimeError("Take Over mode is not active")
        await self.page.keyboard.press(key)

    async def _screenshots(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.page:
                    png = await self.page.screenshot(type="png")
                    await self.emit(
                        "screenshot",
                        image="data:image/png;base64,"
                        + base64.b64encode(png).decode(),
                        width=self.settings.viewport_width,
                        height=self.settings.viewport_height,
                    )
            except Exception:
                pass
            await asyncio.sleep(self.settings.screenshot_interval_seconds)

    async def _state(self) -> tuple[dict[str, Any], bytes | None]:
        assert self.page
        try:
            elements = await self.page.evaluate(DOM_EXTRACTOR)
            self.dom_failures = 0 if elements else self.dom_failures + 1
        except Exception:
            elements = []
            self.dom_failures += 1

        body_text = ""
        try:
            body_text = (
                await self.page.locator("body").inner_text(timeout=2500)
            )[:12000]
        except Exception:
            pass

        screenshot = None
        if self.dom_failures >= 2:
            screenshot = await self.page.screenshot(type="png")

        pages = [
            {"index": i, "url": p.url}
            for i, p in enumerate(self.context.pages if self.context else [])
        ]
        return (
            {
                "url": self.page.url,
                "title": await self.page.title(),
                "text": body_text,
                "elements": elements,
                "tabs": pages,
            },
            screenshot,
        )

    def _prompt(self, state: dict[str, Any]) -> str:
        history = [
            {
                "step": s["step"],
                "action": s["action"],
                "success": s["success"],
                "result": s.get("result"),
            }
            for s in self.steps[-8:]
        ]
        return (
            f"TASK:\n{self.request.task}\n\n"
            f"CURRENT PAGE STATE:\n{json.dumps(state, ensure_ascii=False)}\n\n"
            f"RECENT HISTORY:\n{json.dumps(history, ensure_ascii=False)}"
        )

    async def _guardrails(self, state: dict[str, Any]) -> None:
        haystack = (
            state.get("title", "") + "\n" + state.get("text", "")
        ).lower()

        captcha_terms = [
            "captcha",
            "verify you are human",
            "i'm not a robot",
            "security challenge",
        ]
        auth_terms = [
            "two-factor",
            "2fa",
            "verification code",
            "one-time password",
            "enter otp",
        ]
        login_terms = [
            "sign in to continue",
            "login to continue",
            "log in to continue",
        ]
        payment_terms = [
            "card number",
            "cvv",
            "place order",
            "complete purchase",
            "pay now",
            "checkout",
        ]

        if any(x in haystack for x in captcha_terms + auth_terms + login_terms):
            await self.pause(
                "User action required: CAPTCHA, login, or verification detected"
            )
            await self.emit(
                "needs_user_action",
                reason="authentication_or_captcha",
            )
            await self.pause_event.wait()

        if any(x in haystack for x in payment_terms):
            await self.pause(
                "Payment/checkout detected. Explicit confirmation required before continuing"
            )
            await self.emit(
                "needs_user_action",
                reason="payment_confirmation",
            )
            await self.pause_event.wait()

    async def _human_delay(self) -> None:
        await asyncio.sleep(
            random.uniform(
                self.settings.human_delay_min_ms,
                self.settings.human_delay_max_ms,
            )
            / 1000
        )

    async def _locator(self, target: str):
        assert self.page
        if target.startswith("e"):
            return self.page.locator(
                f'[data-agent-id="{target}"]'
            ).first
        return self.page.locator(target).first

    async def _execute(self, action: AgentAction) -> str:
        assert self.page and self.context

        if action.action == "goto":
            await self.page.goto(
                action.target or action.value or "about:blank",
                wait_until="domcontentloaded",
            )
            return self.page.url

        if action.action == "click":
            await (await self._locator(action.target or "")).click()

        elif action.action == "coordinate_click":
            await self.page.mouse.click(
                float(action.x or 0),
                float(action.y or 0),
            )

        elif action.action == "type":
            loc = await self._locator(action.target or "")
            await loc.click()
            await loc.fill(action.value or "")

        elif action.action == "select":
            await (
                await self._locator(action.target or "")
            ).select_option(label=action.value)

        elif action.action == "check":
            await (await self._locator(action.target or "")).check()

        elif action.action == "uncheck":
            await (await self._locator(action.target or "")).uncheck()

        elif action.action == "hover":
            await (await self._locator(action.target or "")).hover()

        elif action.action == "scroll":
            direction = (action.target or action.value or "down").lower()
            await self.page.mouse.wheel(
                0,
                -650 if "up" in direction else 650,
            )

        elif action.action == "drag":
            src, dst = (action.target or "").split("->", 1)
            await (
                await self._locator(src.strip())
            ).drag_to(await self._locator(dst.strip()))

        elif action.action == "upload_file":
            await (
                await self._locator(action.target or "")
            ).set_input_files(action.value or "")

        elif action.action == "key_press":
            await self.page.keyboard.press(
                action.value or action.target or "Enter"
            )

        elif action.action == "new_tab":
            self.page = await self.context.new_page()
            if action.target or action.value:
                await self.page.goto(
                    action.target or action.value or "about:blank"
                )

        elif action.action == "switch_tab":
            pages = self.context.pages
            index = (
                action.tab_index
                if action.tab_index is not None
                else int(action.target or 0)
            )
            self.page = pages[index]
            await self.page.bring_to_front()

        elif action.action == "close_tab":
            await self.page.close()
            if not self.context.pages:
                self.page = await self.context.new_page()
            else:
                self.page = self.context.pages[-1]

        elif action.action == "extract_text":
            if action.target:
                return (
                    await (
                        await self._locator(action.target)
                    ).inner_text()
                )[:20000]
            return (
                await self.page.locator("body").inner_text()
            )[:20000]

        elif action.action == "wait_for_element":
            await (
                await self._locator(action.target or "body")
            ).wait_for(state="visible")

        elif action.action == "switch_iframe":
            frame = self.page.frame(name=action.target)
            if frame is None:
                frame = next(
                    (
                        f
                        for f in self.page.frames
                        if action.target and action.target in f.url
                    ),
                    None,
                )
            if frame is None:
                raise RuntimeError("iframe not found")
            return f"iframe detected: {frame.url}"

        elif action.action == "done":
            return action.result or "Task completed"

        else:
            raise RuntimeError(f"Unsupported action: {action.action}")

        try:
            await self.page.wait_for_load_state(
                "domcontentloaded",
                timeout=3000,
            )
        except Exception:
            pass

        await self._human_delay()
        return "ok"

    async def run(self) -> dict[str, Any]:
        status = "failed"
        final_result = ""
        playwright = await async_playwright().start()

        try:
            self.browser = await playwright.chromium.launch(
                headless=self.request.headless
            )
            self.context = await self.browser.new_context(
                viewport={
                    "width": self.settings.viewport_width,
                    "height": self.settings.viewport_height,
                }
            )
            self.page = await self.context.new_page()

            def handle_dialog(dialog):
                asyncio.create_task(dialog.dismiss())

            self.page.on("dialog", handle_dialog)
            self._shot_task = asyncio.create_task(self._screenshots())
            await self.emit(
                "status",
                status="running",
                message="Browser launched",
            )

            for step_no in range(1, self.request.max_steps + 1):
                if self.stop_event.is_set():
                    status = "stopped"
                    break

                await self.pause_event.wait()
                state, vision_shot = await self._state()
                await self._guardrails(state)

                if self.stop_event.is_set():
                    status = "stopped"
                    break

                try:
                    action, provider = await asyncio.wait_for(
                        self.router.decide(
                            self._prompt(state),
                            self.request.provider,
                            vision_shot,
                        ),
                        timeout=self.settings.step_timeout_seconds,
                    )
                except Exception as exc:
                    await self.emit(
                        "step",
                        step=step_no,
                        action="llm_decision",
                        provider="none",
                        success=False,
                        reasoning=str(exc),
                    )
                    final_result = str(exc)
                    break

                success = False
                result = ""
                error = ""

                for attempt in range(3):
                    try:
                        result = await asyncio.wait_for(
                            self._execute(action),
                            timeout=self.settings.step_timeout_seconds,
                        )
                        success = True
                        break
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        if attempt < 2:
                            await asyncio.sleep(0.4 * (attempt + 1))

                record = {
                    "step": step_no,
                    "action": action.action,
                    "target": action.target,
                    "value": action.value,
                    "provider": provider,
                    "success": success,
                    "reasoning": action.reasoning,
                    "result": result if success else error,
                }
                self.steps.append(record)
                await self.emit("step", **record)

                if not success:
                    final_result = error
                    break

                if action.action == "done":
                    status = "completed"
                    final_result = action.result or result
                    break
            else:
                status = "failed"
                final_result = "Maximum steps reached before completion"

            return {
                "status": status,
                "result": final_result,
                "steps": self.steps,
                "duration": round(time.monotonic() - self.started, 2),
            }

        finally:
            self.stop_event.set()

            if self._shot_task:
                self._shot_task.cancel()

            if self.context:
                await self.context.close()

            if self.browser:
                await self.browser.close()

            await playwright.stop()
