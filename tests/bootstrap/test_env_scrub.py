from __future__ import annotations

from discode.security.env_scrub import BASE_ALLOWED, scrub_env


class TestScrubEnv:
    def test_keeps_only_base_allowed(self) -> None:
        raw = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "SECRET": "should-be-removed",
            "DATABASE_URL": "postgresql://...",
            "DISCORD_TOKEN": "secret-token",
        }
        result = scrub_env(raw)
        assert "PATH" in result
        assert "HOME" in result
        assert "SECRET" not in result
        assert "DATABASE_URL" not in result
        assert "DISCORD_TOKEN" not in result

    def test_keeps_extra_allowed_keys(self) -> None:
        raw = {
            "PATH": "/usr/bin",
            "MY_CUSTOM_VAR": "value",
            "OTHER_SECRET": "remove-me",
        }
        result = scrub_env(raw, extra_allowed=frozenset({"MY_CUSTOM_VAR"}))
        assert "MY_CUSTOM_VAR" in result
        assert "OTHER_SECRET" not in result
        assert "PATH" in result

    def test_removes_secrets(self) -> None:
        raw = {
            "DATABASE_URL": "postgresql://user:pass@host/db",
            "DISCORD_TOKEN": "my-discord-token",
            "ANTHROPIC_API_KEY": "sk-ant-...",
            "OPENAI_API_KEY": "sk-...",
            "DEPLOYMENT_SECRET_KEY": "fernet-key",
        }
        result = scrub_env(raw)
        assert len(result) == 0

    def test_all_base_allowed_keys_kept(self) -> None:
        raw = {k: f"value-{k}" for k in BASE_ALLOWED}
        raw["EXTRA_SECRET"] = "should-be-removed"
        result = scrub_env(raw)
        for k in BASE_ALLOWED:
            assert k in result
        assert "EXTRA_SECRET" not in result

    def test_empty_env_returns_empty(self) -> None:
        assert scrub_env({}) == {}

    def test_discord_agent_vars_kept(self) -> None:
        raw = {
            "DISCORD_AGENT_SESSION_ID": "sess-123",
            "DISCORD_AGENT_HOOK_URL": "http://127.0.0.1:8787",
            "DISCORD_AGENT_HOOK_SECRET": "secret",
            "DAB_SESSION_ID": "dab-456",
        }
        result = scrub_env(raw)
        assert result == raw

    def test_extra_allowed_is_optional(self) -> None:
        raw = {"PATH": "/usr/bin"}
        result = scrub_env(raw)
        assert result == {"PATH": "/usr/bin"}


def test_infrastructure_secrets_cannot_be_allowlisted() -> None:
    assert (
        scrub_env(
            {"DATABASE_URL": "private", "RUNNER_CONTROL_AUTH": "private"},
            frozenset({"DATABASE_URL", "RUNNER_CONTROL_AUTH"}),
        )
        == {}
    )


def test_execution_policy_requires_explicit_trusted_host() -> None:
    import pytest

    from discode.config import get_settings
    from discode.security.execution_policy import ExecutionDenied, ensure_execution_allowed

    trusted = get_settings().model_copy(update={"SESSION_ISOLATION_MODE": "trusted_host"})
    ensure_execution_allowed(trusted)
    for mode in ("uid_pool", "user_namespace", "platform_equivalent"):
        with pytest.raises(ExecutionDenied, match="unsupported_isolation_mode"):
            ensure_execution_allowed(trusted.model_copy(update={"SESSION_ISOLATION_MODE": mode}))
    with pytest.raises(ExecutionDenied, match="emergency_stop"):
        ensure_execution_allowed(trusted.model_copy(update={"EMERGENCY_STOP": 1}))
