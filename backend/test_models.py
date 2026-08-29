import pytest
from pydantic import ValidationError

from models import RunRequest


def test_run_request_defaults():
    request = RunRequest(task="Open example.com")
    assert request.provider == "auto"
    assert request.headless is True
    assert request.max_steps == 20


@pytest.mark.parametrize("max_steps", [0, 101])
def test_run_request_rejects_invalid_max_steps(max_steps):
    with pytest.raises(ValidationError):
        RunRequest(task="Open example.com", max_steps=max_steps)


def test_run_request_rejects_unknown_provider():
    with pytest.raises(ValidationError):
        RunRequest(task="Open example.com", provider="unknown")
