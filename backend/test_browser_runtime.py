import asyncio
import socket

import httpx
import pytest
from browser_use import Browser
from playwright.async_api import async_playwright


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def wait_for_cdp(port: int) -> str:
    base_url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(timeout=1.0) as client:
        for _ in range(50):
            try:
                response = await client.get(f"{base_url}/json/version")
                if response.is_success:
                    return base_url
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise AssertionError("Chromium CDP endpoint did not become ready")


@pytest.mark.asyncio
async def test_playwright_chromium_can_attach_to_browser_use():
    port = free_port()
    playwright = await async_playwright().start()
    pw_browser = None
    browser_use = None

    try:
        pw_browser = await playwright.chromium.launch(
            headless=True,
            args=[
                f"--remote-debugging-port={port}",
                "--remote-allow-origins=*",
            ],
        )

        context = await pw_browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()
        await page.goto("data:text/html,<title>Chrome Agent Runtime</title><button>Ready</button>")

        cdp_url = await wait_for_cdp(port)
        browser_use = Browser(
            cdp_url=cdp_url,
            keep_alive=True,
            viewport={"width": 1280, "height": 720},
            window_size={"width": 1280, "height": 720},
        )

        await browser_use.start()
        state = await browser_use.get_browser_state_summary(include_screenshot=True)

        assert state is not None
        assert state.url
        assert state.screenshot
        assert browser_use.is_cdp_connected

    finally:
        if browser_use is not None:
            try:
                await browser_use.stop()
            except Exception:
                pass
        if pw_browser is not None:
            try:
                await pw_browser.close()
            except Exception:
                pass
        await playwright.stop()
