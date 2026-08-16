# Adding a coding-agent tool

Discode runs coding-agent CLIs as Discord sessions. Adding one is normally a
**data** change, not a code change: `GenericAdapter` (`src/discode/tools/generic.py`)
drives everything from a row in the `tool_definitions` table. There is no
per-tool Python.

A tool can be added two ways:

- **Seeded** — add an entry to `alembic/seeds/tool_definitions.json`. Applies to
  fresh installs.
- **At runtime** — `/tool register` in Discord (admin-gated, `bot/tool_service.py`).

Either way, **probe it before enabling it**:

```bash
just tool-probe <name>          # static checks, free
just tool-probe <name> --live   # + two real turns, spends API credits
```

A wrong definition does not fail loudly. It fails as a user in Discord getting
truncated output, or a session that silently forgets everything between
messages. The probe exists because both have already shipped here.

## The contract

| Field | Meaning |
|---|---|
| `name` | Primary key, `^[a-z][a-z0-9_-]{0,63}$`. What users type in `/start`. |
| `display_name` | Human label shown in Discord. |
| `argv_prefix` | Argv before any resume/prompt tokens. Element 0 is the binary. |
| `argv_resume_tokens` | Argv that names an existing session. Must contain `{token}` unless `resume_token_source` is `sentinel`. Appended only when a token exists. |
| `argv_suffix` | Must contain `{prompt}`. Substitution is per-token, so a prompt with spaces stays one argv element. |
| `uses_pty` | Allocate a pseudo-terminal. Required by tools that refuse a non-tty stdout (codex, opencode). |
| `resume_token_source` | How multi-turn continuity works — see below. |
| `resume_token_pattern` | Regex (with one capture group) or JSON key, depending on source. |
| `reply_extractor` | How to turn raw stdout into the Discord message. |
| `reply_json_field` | Key to read when `reply_extractor` is `json_field`. |
| `extra_paths` | Extra `PATH` entries for locating the binary. `~` is expanded; missing entries are dropped. |
| `env_passthrough` | **Allowlist** of env var names that survive scrubbing. Everything else is stripped before spawn. |
| `model_flag` | Flag for the per-session model override, e.g. `--model`. |
| `api_key_env` | Env var the per-session encrypted API key is injected as. |
| `base_url_env` | Env var for the per-session base URL override. |
| `enabled` | `false` keeps the row present but unusable. Leave it false until `--live` passes. |

## Choosing `resume_token_source`

This is the field that decides whether a Discord thread is one conversation or
a series of amnesiac one-shots. In rough order of preference:

**`caller_uuid`** — discode mints a UUID at session creation and passes it on
*every* turn, including the first. Use it whenever the tool accepts a
caller-supplied session id that it creates on demand (pi's `--session-id`).
Nothing is parsed from output, so nothing can break it.

```json
"argv_resume_tokens": ["--session-id", "{token}"],
"resume_token_source": "caller_uuid",
"resume_token_pattern": null
```

**`json_field` / `stdout_regex` / `stderr_regex`** — the tool emits its own id
and discode scrapes it after turn one. Works, but couples you to the tool's
output format; codex's `"session id:\s*([0-9a-zA-Z-]+)"` breaks if that line is
ever reworded.

**`sentinel`** — a bare "continue last session" flag with no id
(opencode's `--continue`). Accepted, but **the tool resolves "last session"
itself, usually per working directory**. Two Discord threads on the same repo
can interleave into one conversation. Prefer any of the above.

**`none`** — no continuity. Every message starts a fresh session. Only correct
for genuinely stateless tools.

## Choosing `reply_extractor`

- `stdout_raw` — verbatim stdout.
- `stdout_strip_ansi` — strips CSI/OSC escapes. Use for anything TUI-flavoured.
- `json_field` — parses stdout as **one** JSON object and reads `reply_json_field`.
- `stderr_fallback` — stdout, or stderr when stdout is empty.

`json_field` requires stdout to be a single JSON object. Tools that emit
**JSONL** (one object per line, like `pi --mode json`) do *not* qualify:
`json.loads` fails on the second line, and the extractor silently falls back to
raw stdout, dumping the whole stream into Discord. Check with
`<tool> ... | head -3` before choosing it.

## Procedure

1. Install the binary and confirm the tool has a non-interactive one-shot mode
   and a session-resume handle. Read its `--help`; if it ships local docs, read
   those for the exact env var names rather than guessing.
2. Add the entry with `"enabled": false`.
3. `just tool-probe <name>` — fix every FAIL. Understand every WARN.
4. `just tool-probe <name> --live` — this is the step that actually proves two
   turns share a session. Requires the tool's credentials to be configured.
5. Flip `"enabled": true`.
6. If you are enabling `pi` specifically, note that
   `tests/tools/test_registry.py::test_registry_raises_for_disabled_pi` uses it
   as its disabled-tool fixture and will need repointing at another
   disabled row.

## What the probe checks

Static (free): the definition validates; the binary resolves on the runner's
`PATH`; turn-1 and turn-2 argv are printed for eyeballing; the resume mechanism
is classified; the prompt has an end-of-options guard; `HOME` survives the env
allowlist.

Live (`--live`): both turns spawn through the production `ExecSession`; the
reply extracts non-empty and ANSI-free; a caller-minted token is not clobbered;
and — the real test — a code word given in turn one is recalled in turn two.

## Known systemic warnings

Every current definition trips the flag-injection warning: the prompt is the
last argv element with no `--` before it, so a message consisting of a single
`-`-prefixed token is parsed by the tool as a flag rather than as text. Harmless
against the seeded `claude` entry (which already runs with permissions
bypassed), but it means a member could disable a safety flag on a
hardened definition. Fixing it needs a `--` in `exec_cmd`, gated on tools whose
parsers honour it.
