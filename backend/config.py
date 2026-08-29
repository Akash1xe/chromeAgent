from __future__ import annotations

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    frontend_origin: str = "http://localhost:5173"

    groq_api_key_1: str | None = None
    groq_api_key_2: str | None = None
    groq_api_key_3: str | None = None
    groq_api_key_4: str | None = None
    groq_api_key_5: str | None = None
    gemini_api_key: str | None = None

    groq_model: str = "llama-3.3-70b-versatile"
    gemini_model: str = "gemini-3.7-flash"
    ollama_model: str = "mistral-small"
    ollama_base_url: str = "http://127.0.0.1:11434"

    provider_priority: str = "groq,gemini,ollama"
    screenshot_interval_seconds: float = Field(default=1.25, ge=0.5, le=5.0)
    viewport_width: int = 1440
    viewport_height: int = 900
    step_timeout_seconds: int = 30
    human_delay_min_ms: int = 300
    human_delay_max_ms: int = 900

    logs_dir: Path = BASE_DIR / "backend" / "logs"
    usage_file: Path = BASE_DIR / "backend" / "data" / "usage.json"

    @property
    def groq_keys(self) -> list[str]:
        return [
            key
            for key in [
                self.groq_api_key_1,
                self.groq_api_key_2,
                self.groq_api_key_3,
                self.groq_api_key_4,
                self.groq_api_key_5,
            ]
            if key
        ]

    @property
    def priorities(self) -> list[str]:
        valid = {"groq", "gemini", "ollama"}
        result = [
            p.strip().lower()
            for p in self.provider_priority.split(",")
        ]
        result = [p for p in result if p in valid]
        return result or ["groq", "gemini", "ollama"]


def get_settings() -> Settings:
    settings = Settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    settings.usage_file.parent.mkdir(parents=True, exist_ok=True)
    return settings
