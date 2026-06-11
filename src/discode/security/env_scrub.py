from __future__ import annotations

BASE_ALLOWED: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "TERM",
        "DISCORD_AGENT_SESSION_ID",
        "DISCORD_AGENT_HOOK_URL",
        "DISCORD_AGENT_HOOK_SECRET",
        "DAB_SESSION_ID",
    }
)


def scrub_env(
    raw_env: dict[str, str],
    extra_allowed: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Return copy of raw_env keeping only BASE_ALLOWED | extra_allowed keys."""
    allowed = BASE_ALLOWED | extra_allowed
    return {k: v for k, v in raw_env.items() if k in allowed}
