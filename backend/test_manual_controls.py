import pytest

from browser_agent import BrowserAgent
from config import Settings
from models import RunRequest


async def sink(event):
    return None


class FakeInput:
    def __init__(self):
        self.key_events = []
        self.mouse_events = []

    async def dispatchKeyEvent(self, params, session_id):
        self.key_events.append((params, session_id))

    async def dispatchMouseEvent(self, params, session_id):
        self.mouse_events.append((params, session_id))


class FakeSend:
    def __init__(self, input_api):
        self.Input = input_api


class FakeCDPClient:
    def __init__(self):
        self.input_api = FakeInput()
        self.send = FakeSend(self.input_api)


class FakeSession:
    session_id = "session-1"


class FakeBrowser:
    agent_focus_target_id = "target-1"

    def __init__(self):
        self.cdp_client = FakeCDPClient()

    async def get_or_create_cdp_session(self, target_id, focus=False):
        assert target_id == "target-1"
        assert focus is False
        return FakeSession()


def make_agent():
    settings = Settings(_env_file=None)
    agent = BrowserAgent(
        "run-1",
        RunRequest(task="manual controls"),
        settings,
        router=object(),
        sink=sink,
    )
    agent.takeover = True
    agent.browser = FakeBrowser()
    return agent


@pytest.mark.asyncio
async def test_manual_key_chord_holds_control_for_final_key():
    agent = make_agent()

    await agent.manual_key("Control+A")

    events = agent.browser.cdp_client.input_api.key_events
    assert [event[0]["type"] for event in events] == [
        "keyDown",
        "keyDown",
        "keyUp",
        "keyUp",
    ]
    assert events[0][0]["key"] == "Control"
    assert events[1][0]["key"] == "A"
    assert events[1][0]["modifiers"] == 2
    assert events[2][0]["modifiers"] == 2
    assert events[3][0]["key"] == "Control"
    assert events[3][0]["modifiers"] == 0


@pytest.mark.asyncio
async def test_manual_scroll_dispatches_mouse_wheel():
    agent = make_agent()

    await agent.manual_scroll(0, 650)

    events = agent.browser.cdp_client.input_api.mouse_events
    assert len(events) == 1
    params, session_id = events[0]
    assert params["type"] == "mouseWheel"
    assert params["deltaX"] == 0
    assert params["deltaY"] == 650
    assert session_id == "session-1"


@pytest.mark.asyncio
async def test_manual_controls_require_takeover():
    agent = make_agent()
    agent.takeover = False

    with pytest.raises(RuntimeError):
        await agent.manual_key("Enter")

    with pytest.raises(RuntimeError):
        await agent.manual_scroll(0, 100)
