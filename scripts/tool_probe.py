"""Conformance-probe a ToolDefinition against the real tool binary.

Adding a tool to discode is a data change (a row in ``tool_definitions``), which
means a wrong definition is only discovered when a user's session misbehaves in
Discord. This probe closes that gap: it builds argv exactly as the runner does,
spawns the tool through the production ``ExecSession``, and checks that the
contract actually holds.

Usage::

    just tool-probe pi              # static checks only - free, no API calls
    just tool-probe pi --live       # + real two-turn run (spends API credits)

Static checks need nothing but the binary. ``--live`` is opt-in because it costs
money and requires the tool's credentials to be configured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from discode.security.env_scrub import scrub_env  # noqa: E402
from discode.tools.errors import BadToolDefinition  # noqa: E402
from discode.tools.exec_session import ExecFailed, ExecSession, ExecTimeout  # noqa: E402
from discode.tools.generic import _ANSI_RE, GenericAdapter  # noqa: E402

SEEDS = Path(__file__).resolve().parent.parent / "alembic" / "seeds" / "tool_definitions.json"

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
_ICON = {PASS: "✓", FAIL: "✗", WARN: "!", SKIP: "-"}

_results: list[tuple[str, str, str]] = []


def check(status: str, name: str, detail: str = "") -> None:
    _results.append((status, name, detail))
    print(f"  {_ICON[status]} {status:4} {name}" + (f" - {detail}" if detail else ""))


def load_definition(name: str, path: Path) -> Any:
    rows = json.loads(path.read_text())
    for row in rows:
        if row["name"] == name:
            return SimpleNamespace(**row)
    sys.exit(f"no tool named {name!r} in {path} (have: {', '.join(r['name'] for r in rows)})")


def resolve_binary(definition: Any) -> str | None:
    """Mirror the runner's PATH construction (saga_create) closely enough to
    answer: would the runner find this binary?"""
    extra = [os.path.expanduser(p) for p in (definition.extra_paths or [])]
    path = os.pathsep.join([*extra, os.environ.get("PATH", "")])
    return shutil.which(definition.argv_prefix[0], path=path)


def static_checks(definition: Any) -> GenericAdapter | None:
    print("\nStatic checks (no API calls)")

    try:
        adapter = GenericAdapter(definition)
        check(PASS, "definition validates")
    except BadToolDefinition as exc:
        check(FAIL, "definition validates", str(exc))
        return None

    binary = resolve_binary(definition)
    if binary:
        check(PASS, "binary on PATH", binary)
    else:
        check(FAIL, "binary on PATH", f"{definition.argv_prefix[0]!r} not found")

    token = adapter.mint_initial_token()
    source = definition.resume_token_source

    first = adapter.exec_cmd("PROMPT", resume_token=token)
    later = adapter.exec_cmd("PROMPT", resume_token=token or "TOKEN")
    print(f"      turn 1 argv: {first}")
    print(f"      turn 2 argv: {later}")

    # Multi-turn continuity: does turn 2 differ from a cold start in a way that
    # names the earlier conversation?
    if source == "none":
        check(WARN, "multi-turn continuity", "resume_token_source=none - every turn starts fresh")
    elif source == "caller_uuid":
        if token and token in first:
            check(PASS, "multi-turn continuity", "caller-assigned id present from turn 1")
        else:
            check(FAIL, "multi-turn continuity", "caller_uuid minted no token into turn-1 argv")
    elif source == "sentinel":
        check(
            WARN,
            "multi-turn continuity",
            "sentinel resume is cwd-scoped - two threads sharing a cwd can interleave",
        )
    else:
        check(PASS, "multi-turn continuity", f"token scraped via {source}")

    # The prompt lands as a bare argv element. Without an end-of-options
    # separator a prompt beginning with '-' is parsed as a flag by the tool.
    if "--" not in definition.argv_prefix and "--" not in definition.argv_resume_tokens:
        check(
            WARN,
            "prompt flag-injection guard",
            "no '--' before {prompt}; a prompt starting with '-' is read as a flag",
        )
    else:
        check(PASS, "prompt flag-injection guard")

    scrubbed = scrub_env(os.environ.copy(), extra_allowed=frozenset(adapter.env_allowlist()))
    if "HOME" in scrubbed:
        check(PASS, "env allowlist includes HOME", "tool can reach its config/credential store")
    else:
        check(WARN, "env allowlist includes HOME", "tools usually need HOME for credentials")

    return adapter


async def live_checks(adapter: GenericAdapter, definition: Any, timeout: float) -> None:
    print("\nLive checks (spends API credits)")

    nonce = f"ZX{uuid.uuid4().hex[:6].upper()}"
    token = adapter.mint_initial_token()
    env = scrub_env(os.environ.copy(), extra_allowed=frozenset(adapter.env_allowlist()))

    with tempfile.TemporaryDirectory(prefix="discode-probe-") as cwd:
        env["HOME"] = env.get("HOME", cwd)

        async def turn(prompt: str, tok: str | None) -> Any:
            argv = adapter.exec_cmd(prompt, resume_token=tok)
            return await ExecSession(
                argv=argv, cwd=cwd, env=env, use_pty=adapter.exec_uses_pty()
            ).run(timeout=timeout)

        try:
            first = await turn(f"Remember this code word: {nonce}. Reply with just: OK", token)
        except (ExecFailed, ExecTimeout) as exc:
            check(FAIL, "turn 1 runs", str(exc)[:220])
            return
        check(PASS, "turn 1 runs", f"exit={first.exit_code}")

        reply = adapter.extract_reply(first.text, first.stderr)
        if reply.strip():
            check(PASS, "reply extracts non-empty", f"{reply.strip()[:60]!r}")
        else:
            check(FAIL, "reply extracts non-empty", "extractor produced empty text")

        if _ANSI_RE.search(reply):
            check(FAIL, "reply is ANSI-free", "escape sequences would reach Discord verbatim")
        else:
            check(PASS, "reply is ANSI-free")

        scraped = adapter.parse_resume_token(first.text, first.stderr)
        next_token = scraped or token
        if definition.resume_token_source == "caller_uuid":
            if scraped is None:
                check(PASS, "minted token survives turn 1", "parse_resume_token left it alone")
            else:
                check(FAIL, "minted token survives turn 1", f"clobbered by {scraped!r}")
        elif next_token:
            check(PASS, "resume token obtained", f"{str(next_token)[:40]!r}")
        else:
            check(FAIL, "resume token obtained", "no token for turn 2 - continuity impossible")

        try:
            second = await turn("What code word did I ask you to remember? Reply with just it.", next_token)
        except (ExecFailed, ExecTimeout) as exc:
            check(FAIL, "turn 2 runs", str(exc)[:220])
            return
        check(PASS, "turn 2 runs", f"exit={second.exit_code}")

        recalled = adapter.extract_reply(second.text, second.stderr)
        if nonce in recalled:
            check(PASS, "two turns share one session", f"recalled {nonce}")
        else:
            check(
                FAIL,
                "two turns share one session",
                f"{nonce} not in reply {recalled.strip()[:80]!r} - resume is not working",
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tool", help="tool name as it appears in the seed file")
    ap.add_argument("--live", action="store_true", help="run two real turns (spends credits)")
    ap.add_argument("--seeds", type=Path, default=SEEDS)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    definition = load_definition(args.tool, args.seeds)
    print(f"Probing {definition.name!r} ({definition.display_name}) from {args.seeds}")
    if not definition.enabled:
        print("  note: this definition is seeded disabled - it is not offered in Discord yet")

    adapter = static_checks(definition)

    if adapter is None:
        pass
    elif args.live:
        asyncio.run(live_checks(adapter, definition, args.timeout))
    else:
        print("\nLive checks skipped - pass --live to run two real turns (spends credits)")
        check(SKIP, "two-turn continuity unverified")

    failures = [r for r in _results if r[0] == FAIL]
    warnings = [r for r in _results if r[0] == WARN]
    print(
        f"\n{len(_results) - len(failures) - len(warnings)} passed, "
        f"{len(warnings)} warning(s), {len(failures)} failure(s)"
    )
    if failures:
        print("Do NOT enable this tool until the failures are fixed.")
        return 1
    if not args.live:
        print("Static checks passed. Re-run with --live before setting enabled=true.")
    else:
        print("Conformance passed. Safe to set enabled=true in the seed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
