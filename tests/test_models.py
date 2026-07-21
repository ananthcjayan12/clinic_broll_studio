from clinic_broll.core.models import TASK_CATALOG, validate_model_map


def test_every_task_accepts_each_provider_with_valid_effort():
    for provider, model, effort in [
        ("grok_cli", "grok-4.5", "high"),
        ("codex_cli", "authenticated-default", "xhigh"),
        ("claude_cli", "sonnet", "medium"),
        ("kimi_api", "kimi-k2.6", "high"),
    ]:
        model_map = {
            task: {"provider": provider, "model": model, "reasoning_effort": effort}
            for task in TASK_CATALOG
        }
        validated = validate_model_map(model_map)
        assert set(validated) == set(TASK_CATALOG)
        assert all(value["provider"] == provider for value in validated.values())


def test_invalid_provider_is_rejected():
    model_map = {
        task: {"provider": "unknown", "model": "x", "reasoning_effort": "high"}
        for task in TASK_CATALOG
    }
    try:
        validate_model_map(model_map)
    except ValueError as exc:
        assert "Unsupported provider" in str(exc)
    else:
        raise AssertionError("invalid provider should fail")
