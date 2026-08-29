from browser_use import Tools


def test_pinned_browser_use_exposes_required_core_tools():
    tools = Tools()
    actions = set(tools.registry.registry.actions)

    required = {
        "search",
        "navigate",
        "go_back",
        "wait",
        "input",
        "upload_file",
        "switch",
        "close",
        "extract",
        "scroll",
        "send_keys",
        "find_text",
        "screenshot",
        "dropdown_options",
        "select_dropdown",
        "evaluate",
        "done",
        "click",
    }

    missing = required - actions
    assert not missing, f"Browser Use is missing required actions: {sorted(missing)}"


def test_hover_and_drag_fallback_tool_is_available():
    tools = Tools()
    actions = set(tools.registry.registry.actions)

    # browser-use 0.13.8 intentionally handles hover/drag through evaluate()
    # rather than dedicated hover/drag actions.
    assert "evaluate" in actions
