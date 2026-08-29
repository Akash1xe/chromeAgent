from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

ProviderName = Literal["auto", "groq", "gemini", "ollama"]


class RunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=10_000)
    provider: ProviderName = "auto"
    headless: bool = True
    max_steps: int = Field(default=20, ge=1, le=100)
    uploaded_files: list[str] = Field(default_factory=list, max_length=10)


class AgentAction(BaseModel):
    action: Literal[
        "goto", "click", "type", "select", "check", "uncheck", "scroll",
        "hover", "drag", "upload_file", "key_press", "new_tab", "switch_tab",
        "close_tab", "extract_text", "wait_for_element", "switch_iframe",
        "coordinate_click", "done"
    ]
    target: str | None = None
    value: str | None = None
    x: float | None = None
    y: float | None = None
    tab_index: int | None = None
    reasoning: str = ""
    result: str | None = None


class ManualClick(BaseModel):
    x: float
    y: float


class ManualType(BaseModel):
    text: str


class ManualKey(BaseModel):
    key: str = Field(min_length=1, max_length=64)


class ManualScroll(BaseModel):
    delta_x: float = Field(default=0, ge=-5000, le=5000)
    delta_y: float = Field(ge=-5000, le=5000)


class KeyUpdate(BaseModel):
    name: Literal[
        "GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3",
        "GROQ_API_KEY_4", "GROQ_API_KEY_5", "GEMINI_API_KEY"
    ]
    value: str


class PriorityUpdate(BaseModel):
    providers: list[Literal["groq", "gemini", "ollama"]]
