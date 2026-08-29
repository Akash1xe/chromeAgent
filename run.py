from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def python_executable() -> str:
    venv = ROOT / ".venv"
    if os.name == "nt":
        candidate = venv / "Scripts" / "python.exe"
    else:
        candidate = venv / "bin" / "python"

    if candidate.exists():
        return str(candidate)
    return sys.executable


def npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def run_doctor() -> None:
    result = subprocess.run(
        [python_executable(), str(BACKEND / "doctor.py")],
        cwd=BACKEND,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            "\nRequired environment checks failed. "
            "Fix the FAIL items above, then run this launcher again."
        )


def start_processes() -> list[subprocess.Popen]:
    backend = subprocess.Popen(
        [
            python_executable(),
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=BACKEND,
    )

    frontend = subprocess.Popen(
        [npm_command(), "run", "dev", "--", "--host", "127.0.0.1"],
        cwd=FRONTEND,
    )

    return [backend, frontend]


def stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()

    deadline = time.time() + 5
    for process in reversed(processes):
        if process.poll() is not None:
            continue
        remaining = max(0.1, deadline - time.time())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Chrome Agent backend and frontend")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the dashboard automatically")
    parser.add_argument("--skip-doctor", action="store_true", help="Skip startup diagnostics")
    args = parser.parse_args()

    if not args.skip_doctor:
        run_doctor()

    processes = start_processes()

    def shutdown(*_args):
        stop_processes(processes)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    print("\nChrome Agent is starting:")
    print("  Backend:  http://127.0.0.1:8000")
    print("  Frontend: http://127.0.0.1:5173")
    print("Press Ctrl+C to stop both services.\n")

    if not args.no_browser:
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:5173")

    try:
        while True:
            for process in processes:
                code = process.poll()
                if code is not None:
                    print(f"A child process exited with code {code}. Stopping Chrome Agent.")
                    stop_processes(processes)
                    return code
            time.sleep(0.5)
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
