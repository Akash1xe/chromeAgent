from __future__ import annotations

import argparse
import asyncio
import statistics
import time

from playwright.async_api import async_playwright


async def measure_once(url: str, timeout_ms: int) -> dict[str, float | str]:
    started = time.perf_counter()
    playwright = await async_playwright().start()
    launch_started = time.perf_counter()
    browser = await playwright.chromium.launch(headless=True)
    launch_seconds = time.perf_counter() - launch_started

    try:
        context = await browser.new_context()
        page = await context.new_page()
        nav_started = time.perf_counter()
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        navigation_seconds = time.perf_counter() - nav_started
        total_seconds = time.perf_counter() - started

        return {
            "url": page.url,
            "title": await page.title(),
            "launch_seconds": round(launch_seconds, 3),
            "navigation_seconds": round(navigation_seconds, 3),
            "total_seconds": round(total_seconds, 3),
        }
    finally:
        await browser.close()
        await playwright.stop()


async def main_async() -> int:
    parser = argparse.ArgumentParser(
        description="Measure Chrome Agent direct-browser startup/navigation performance"
    )
    parser.add_argument(
        "--url",
        default="https://www.flipkart.com",
        help="URL to benchmark",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        choices=range(1, 6),
        metavar="1-5",
    )
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    results = []
    print(f"Benchmarking {args.url} for {args.runs} run(s)...\n")

    for index in range(args.runs):
        try:
            result = await measure_once(args.url, args.timeout * 1000)
        except Exception as exc:
            print(f"Run {index + 1}: FAILED - {type(exc).__name__}: {exc}")
            return 1

        results.append(result)
        print(
            f"Run {index + 1}: "
            f"launch={result['launch_seconds']}s, "
            f"navigate={result['navigation_seconds']}s, "
            f"total={result['total_seconds']}s, "
            f"url={result['url']}"
        )

    launches = [float(item["launch_seconds"]) for item in results]
    navigations = [float(item["navigation_seconds"]) for item in results]
    totals = [float(item["total_seconds"]) for item in results]

    print("\nMedian timings:")
    print(f"  Chromium launch: {statistics.median(launches):.3f}s")
    print(f"  Navigation:      {statistics.median(navigations):.3f}s")
    print(f"  Total:           {statistics.median(totals):.3f}s")
    print("\nThis benchmark does not call Groq, Gemini, or Ollama.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
