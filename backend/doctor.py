from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path

import httpx

from config import BASE_DIR, get_settings


def check(name: str, ok: bool, detail: str, *, required: bool = True) -> dict:
    return {
        "name": name,
        "ok": bool(ok),
        "required": required,
        "detail": detail,
    }


def run_checks() -> list[dict]:
    settings = get_settings()
    results: list[dict] = []

    py_ok = sys.version_info >= (3, 11)
    results.append(
        check(
            "Python",
            py_ok,
            f"{platform.python_version()} ({sys.executable})",
        )
    )

    try:
        import fastapi  # noqa: F401
        import browser_use  # noqa: F401
        import playwright  # noqa: F401
        results.append(check("Python packages", True, "FastAPI, Browser Use and Playwright import correctly"))
    except Exception as exc:
        results.append(check("Python packages", False, f"{type(exc).__name__}: {exc}"))

    env_path = BASE_DIR / ".env"
    results.append(
        check(
            ".env",
            env_path.exists(),
            str(env_path) if env_path.exists() else "Missing .env; copy .env.example to .env",
            required=False,
        )
    )

    groq_count = len(settings.groq_keys)
    results.append(
        check(
            "Groq keys",
            groq_count > 0,
            f"{groq_count} configured",
            required=False,
        )
    )

    results.append(
        check(
            "Gemini key",
            bool(settings.gemini_api_key),
            "configured" if settings.gemini_api_key else "not configured",
            required=False,
        )
    )

    npm = shutil.which("npm")
    results.append(
        check(
            "npm",
            npm is not None,
            npm or "npm not found in PATH",
        )
    )

    frontend_modules = BASE_DIR / "frontend" / "node_modules"
    results.append(
        check(
            "Frontend dependencies",
            frontend_modules.exists(),
            str(frontend_modules) if frontend_modules.exists() else "Run npm install in frontend/",
            required=False,
        )
    )

    chromium_ok = False
    chromium_detail = "Chromium executable not detected"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            executable = Path(p.chromium.executable_path)
            chromium_ok = executable.exists()
            chromium_detail = str(executable)
    except Exception as exc:
        chromium_detail = f"{type(exc).__name__}: {exc}"

    results.append(
        check(
            "Playwright Chromium",
            chromium_ok,
            chromium_detail if chromium_ok else chromium_detail + " — run: playwright install chromium",
        )
    )

    ollama_ok = False
    ollama_detail = settings.ollama_base_url
    try:
        response = httpx.get(
            f"{settings.ollama_base_url.rstrip('/')}/api/tags",
            timeout=1.5,
        )
        ollama_ok = response.is_success
        if ollama_ok:
            data = response.json()
            models = [item.get("name", "") for item in data.get("models", [])]
            wanted = settings.ollama_model.lower()
            model_present = any(wanted in model.lower() for model in models)
            ollama_detail = (
                f"reachable; {settings.ollama_model} installed"
                if model_present
                else f"reachable; {settings.ollama_model} not installed"
            )
        else:
            ollama_detail = f"HTTP {response.status_code}"
    except Exception:
        ollama_detail = "not reachable (optional unless Ollama fallback is needed)"

    results.append(
        check(
            "Ollama",
            ollama_ok,
            ollama_detail,
            required=False,
        )
    )

    return results


def main() -> int:
    results = run_checks()
    print("\nChrome Agent Doctor\n")
    for item in results:
        icon = "OK" if item["ok"] else ("WARN" if not item["required"] else "FAIL")
        print(f"[{icon:4}] {item['name']}: {item['detail']}")

    required_failures = [item for item in results if item["required"] and not item["ok"]]
    print()
    if required_failures:
        print(f"{len(required_failures)} required check(s) failed.")
        return 1

    print("Required checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
