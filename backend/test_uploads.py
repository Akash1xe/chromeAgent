from pathlib import Path

from fastapi.testclient import TestClient

import main
from browser_agent import BrowserAgent
from config import Settings
from models import RunRequest


async def sink(event):
    return None


def test_upload_endpoint_sanitizes_filename(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(main, "UPLOADS_DIR", uploads)

    client = TestClient(main.app)
    response = client.post(
        "/api/uploads",
        files={"file": ("../dangerous name.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["original_name"] == "dangerous name.txt"
    assert ".." not in data["name"]
    assert "/" not in data["name"]
    assert "\\" not in data["name"]

    saved = uploads / data["name"]
    assert saved.exists()
    assert saved.read_bytes() == b"hello"


def test_agent_receives_exact_existing_file_paths_only(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()

    allowed = uploads / "allowed.txt"
    allowed.write_text("ok", encoding="utf-8")
    (uploads / "folder").mkdir()

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    agent = BrowserAgent(
        "run-1",
        RunRequest(
            task="Upload the attached file",
            uploaded_files=[
                "allowed.txt",
                "missing.txt",
                "folder",
                "../outside.txt",
            ],
        ),
        Settings(_env_file=None),
        router=object(),
        sink=sink,
    )

    resolved = agent._resolve_uploaded_files(uploads)

    assert resolved == [str(allowed.resolve())]
    assert str(outside.resolve()) not in resolved
    assert str(uploads.resolve()) not in resolved
