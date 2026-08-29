import pytest

from browser_agent import BrowserAgent
from config import Settings
from models import RunRequest


async def sink(event):
    return None


class DummyRouter:
    def __init__(self):
        self.vision_required = False
        self.last_provider = None


class DummyAgentSettings:
    use_vision = "auto"


class DummyAgent:
    def __init__(self):
        self.settings = DummyAgentSettings()


def make_agent(task="Open Flipkart"):
    return BrowserAgent(
        "run-1",
        RunRequest(task=task),
        Settings(_env_file=None),
        DummyRouter(),
        sink,
    )


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("Open Flipkart", "https://www.flipkart.com"),
        ("open google", "https://www.google.com"),
        ("Go to github", "https://github.com"),
        ("Navigate to https://example.com", "https://example.com"),
        ("Open example.com", "https://example.com"),
    ],
)
def test_simple_navigation_fast_path_detection(task, expected):
    assert BrowserAgent._simple_navigation_url(task) == expected


@pytest.mark.parametrize(
    "task",
    [
        "Open Flipkart and search for laptops",
        "Search Google for browser-use",
        "Compare Amazon and Flipkart prices",
        "Open a website",
    ],
)
def test_complex_tasks_do_not_use_navigation_fast_path(task):
    assert BrowserAgent._simple_navigation_url(task) is None


@pytest.mark.asyncio
async def test_vision_enables_after_two_dom_failures_and_recovers():
    browser_agent = make_agent("Do something complex")
    agent = DummyAgent()

    await browser_agent._set_dom_health(agent, False)
    assert browser_agent.dom_failures == 1
    assert browser_agent.router.vision_required is False
    assert agent.settings.use_vision == "auto"

    await browser_agent._set_dom_health(agent, False)
    assert browser_agent.dom_failures == 2
    assert browser_agent.router.vision_required is True
    assert agent.settings.use_vision is True

    await browser_agent._set_dom_health(agent, True)
    assert browser_agent.dom_failures == 0
    assert browser_agent.router.vision_required is False
    assert agent.settings.use_vision == "auto"
