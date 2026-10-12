"""Exercise the documented probe as a real CLI, including its import boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "tool_probe.py"


def _definition(*, source: str = "caller_uuid", binary: str | None = None) -> dict[str, object]:
    return {
        "name": "sample",
        "display_name": "Sample",
        "argv_prefix": [binary or sys.executable],
        "argv_resume_tokens": ["--session-id", "{token}"],
        "argv_suffix": ["{prompt}"],
        "uses_pty": False,
        "resume_token_source": source,
        "resume_token_pattern": None,
        "reply_extractor": "stdout_raw",
        "reply_json_field": None,
        "extra_paths": [],
        "env_passthrough": ["HOME"],
        "model_flag": None,
        "api_key_env": None,
        "base_url_env": None,
        "enabled": False,
    }


def _probe(
    tmp_path: Path, definition: dict[str, object], *args: str
) -> subprocess.CompletedProcess[str]:
    seeds = tmp_path / "tools.json"
    seeds.write_text(json.dumps([definition]))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "sample", "--seeds", str(seeds), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_static_tool_probe_runs_without_provider_calls(tmp_path: Path) -> None:
    result = _probe(tmp_path, _definition())
    assert result.returncode == 0, result.stderr
    assert "definition validates" in result.stdout
    assert "two-turn continuity unverified" in result.stdout
    assert "Traceback" not in result.stderr


def test_failed_static_checks_prevent_live_probe(tmp_path: Path) -> None:
    result = _probe(
        tmp_path,
        _definition(binary="missing-discode-probe-binary"),
        "--live",
    )
    assert result.returncode == 1
    assert "Live checks skipped because static checks failed" in result.stdout
    assert "Live checks (spends API credits)" not in result.stdout


def test_disabled_sentinel_mode_fails_without_traceback(tmp_path: Path) -> None:
    definition = _definition(source="sentinel")
    definition["argv_resume_tokens"] = ["--continue"]
    result = _probe(tmp_path, definition)
    assert result.returncode == 1
    assert "sentinel resume is disabled" in result.stdout
    assert "Traceback" not in result.stderr
