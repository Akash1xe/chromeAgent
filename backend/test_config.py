from config import Settings


def test_default_provider_priority():
    settings = Settings(_env_file=None)
    assert settings.priorities == ["groq", "gemini", "ollama"]


def test_provider_priority_filters_invalid_entries():
    settings = Settings(
        _env_file=None,
        provider_priority="ollama,invalid,groq,gemini",
    )
    assert settings.priorities == ["ollama", "groq", "gemini"]


def test_groq_keys_ignore_empty_values():
    settings = Settings(
        _env_file=None,
        groq_api_key_1="first",
        groq_api_key_2="",
        groq_api_key_3=None,
        groq_api_key_4="fourth",
    )
    assert settings.groq_keys == ["first", "fourth"]
