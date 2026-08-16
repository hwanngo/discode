# Discode

A Discord bot platform that exposes AI coding-assistant CLIs (Claude, Codex, OpenCode, Droid, Pi,
plus any DB-registered tool) as chat sessions. Each Discord thread becomes a stateful session
backed by one of the registered tools; conversations flow naturally back and forth between humans
and the assistant.

## How it works

Every user message in a session thread spawns one short-lived subprocess for whichever tool is
registered to that session, captures its stdout, and posts the reply back to Discord through the
runner's loopback control API. Multi-turn continuity is preserved by threading per-tool resume
tokens across turns. Any tool registered via `/tool register` (or seeded at migration time)
participates in this pipeline automatically.

There is no long-lived per-session subprocess to crash, no port files, no streaming pipeline —
just one process per turn, one HTTP POST per reply.

```
Discord thread message
      │
      ▼
discode-bot ─► outbox (input:jobs:<host>) ─► discode-runner
                                                  │
                                                  ▼
                                  saga_input — idempotent claim
                                                  │
                                                  ▼
                              ExecSession.run(prompt)        ← spawns registered tool subprocess
                                                  │
                                                  ▼
                              ReplyClient.post_reply()        ← POST /v1/sessions/{sid}/reply
                                                  │
                                                  ▼
                              DiscordRestClient.send_message  ← rate-limited
                                                  │
                                                  ▼
                                              Discord
```

## Processes

| Process              | Entry point        | Role                                                                  |
| -------------------- | ------------------ | --------------------------------------------------------------------- |
| `discode-bot`        | `just bot`         | Discord gateway; slash commands and thread message routing            |
| `discode-runner`     | `just runner`      | Spawns tool subprocesses; hosts the loopback control API on `:8788`   |
| `discode-dispatcher` | `just dispatcher`  | Drains the outbox to Discord (system notices, archive, terminal)      |
| `discode-janitor`    | `just janitor`     | Periodic sweeps: TTL, watchdogs, thread-bind repair                   |

All four share one Postgres database and one Redis instance.

## Quick start (local dev)

**Prerequisites:** Python ≥ 3.14, [uv](https://docs.astral.sh/uv/),
[just](https://just.systems/), Docker, [overmind](https://github.com/DarthSim/overmind),
and one or more tool CLIs (`claude`, `codex`, `opencode`, etc.) on `PATH`.

```bash
# 1. Install dependencies
just install

# 2. Start Postgres + Redis containers
just infra

# 3. Configure
cp .env.example .env
$EDITOR .env

# 4. Run migrations
just migrate

# 5. Start every process
just dev
```

Stop with `Ctrl-C` inside the overmind session, or `overmind stop` from another terminal.

## Bot setup (server admin, one-time per guild)

```
/setup init                                  # register the guild
/setup admin-role set @YourAdminRole         # lock down further /setup commands
/setup map set claude #coding-claude         # map each tool to a channel
/setup map set codex  #coding-codex
/setup map set opencode #coding-opencode
/root add /home/user/projects myrepo         # allow a project root (optionally register alias)
/tool list                                   # confirm seeded tools are visible
```

`/setup admin-role list` and `/setup map list` show current state.

**Admin authorization fails closed.** Until an admin role is configured, the only admin commands
that work are `/setup init` and `/setup admin-role set`, and both require the Discord **Manage
Server** permission. Every other admin command (`/setup map`, `/setup sync`, `/tool …`,
`/root alias …`, `/session config …`) is **denied** until an admin role exists — so you must run
`/setup admin-role set` before the rest of setup. A user with **Manage Server** can always
overwrite the admin role (bootstrap override). The bot syncs slash commands automatically on
startup — `/setup sync` is only needed to force a re-sync after an unexpected drift.

## Session usage

```
/start claude my-feature /home/user/projects/myrepo
```

| Parameter | Meaning                                                                               |
| --------- | ------------------------------------------------------------------------------------- |
| `tool`    | Any tool registered in the guild (`claude`, `codex`, `opencode`, `droid`, …)          |
| `name`    | Human-readable label (also used for archive resume by name)                           |
| `cwd`     | Absolute path **or** a registered alias name (see `/root alias set` to register one)  |

A public thread is created in the mapped channel. **Multiple sessions may share the same `cwd`**
— each turn is its own subprocess, so different tools or different sessions of the same tool
coexist freely.

Once the thread exists, any member of the session can post in it; each message is sent to the
underlying tool as the next prompt. Set `ENABLE_MESSAGE_CONTENT_INPUT=true` in `.env` to enable
message-content routing (default: off).

## Command reference

### Setup (guild-scoped, admin-only after `/setup admin-role set`)

| Command                            | Description                                       |
| ---------------------------------- | ------------------------------------------------- |
| `/setup init`                      | Register this guild                               |
| `/setup admin-role set @role`      | Set the admin role                                |
| `/setup admin-role list`           | Show the admin-role policy                        |
| `/setup map set <tool> #channel`   | Map a tool to a Discord channel                   |
| `/setup map list`                  | List all tool→channel mappings                    |
| `/setup map remove <tool>`         | Remove a tool→channel mapping                     |
| `/setup sync`                      | Re-register slash commands with Discord           |

### Sessions

| Command                                | Gate              | Description                                                  |
| -------------------------------------- | ----------------- | ------------------------------------------------------------ |
| `/start <tool> <name> <cwd>`           | open              | Start a session in the given cwd (path or alias)            |
| `/help`                                | open              | List all available Discode commands                          |
| `/diff [session]`                      | open              | Show a git diff preview for a session                        |
| `/invite <user> [session]`             | owner or admin    | Invite a user as session member                              |
| `/send <text> [session]`               | member or admin   | Inject text into a session                                   |
| `/stop [session]`                      | owner or admin    | Stop a session (rejects future input)                        |
| `/archive [session]`                   | owner or admin    | Archive a session (preserves resume token)                   |
| `/resume [session]`                    | owner or admin    | Resume an archived session                                   |
| `/restart [session]`                   | owner or admin    | Clear conversation context (keeps the thread)                |

### Tool registration (admin)

| Command                                           | Description                                |
| ------------------------------------------------- | ------------------------------------------ |
| `/tool list`                                      | List registered tools (open to all)        |
| `/tool show <name>`                               | Show full tool config (open to all)        |
| `/tool register <name> <json_config>`             | Register a new tool definition             |
| `/tool update <name> <field> <value>`             | Edit one field of a tool                   |
| `/tool enable <name>`                             | Enable a tool                              |
| `/tool disable <name>`                            | Disable a tool                             |
| `/tool remove <name>`                             | Remove a tool (rejects if sessions exist)  |

### Per-session overrides (admin)

| Command                                | Description                                                    |
| -------------------------------------- | -------------------------------------------------------------- |
| `/session config set <key> <value>`    | Set `model`/`base_url`/`api_key` override (in-thread only; `base_url` is validated — public http(s) only, private/loopback/metadata hosts rejected) |
| `/session config get`                  | Show overrides set on the current session thread               |
| `/session config clear <key>`          | Clear one override                                             |
| `/session config clear-all`            | Clear all overrides                                            |

### Roots and aliases

| Command                                | Gate                  | Description                                                 |
| -------------------------------------- | --------------------- | ----------------------------------------------------------- |
| `/root add <path> [alias]`             | open / admin if alias | Allow a project directory; optionally register a guild alias (credential/system paths are denied — see Security model) |
| `/root alias set <alias> <path>`       | admin                 | Register or replace a guild alias                           |
| `/root alias list`                     | open                  | List all guild aliases                                      |
| `/root alias remove <alias>`           | admin                 | Remove an alias                                             |

## Configuration

All configuration is via environment variables (loaded from `.env`). Required:

| Variable                 | Purpose                                                            |
| ------------------------ | ------------------------------------------------------------------ |
| `DISCORD_TOKEN`          | Bot token from the Discord developer portal                        |
| `DATABASE_URL`           | `postgresql+asyncpg://...`                                         |
| `REDIS_URL`              | `redis://...`                                                      |
| `DEPLOYMENT_SECRET_KEY`  | Fernet key (or comma-separated list for rotation); used to encrypt per-session `api_key` overrides |
| `SESSION_ISOLATION_MODE` | `uid_pool` / `user_namespace` / `platform_equivalent`              |

Useful optional knobs:

| Variable                        | Default          | Purpose                                                   |
| ------------------------------- | ---------------- | --------------------------------------------------------- |
| `RUNNER_CONTROL_BIND`           | `127.0.0.1:8788` | Address the runner's control API listens on              |
| `RUNNER_CONTROL_AUTH`           | _(empty)_        | Required bearer token when bind is non-loopback           |
| `ENABLE_MESSAGE_CONTENT_INPUT`  | `false`          | Toggle Discord message-content routing                    |
| `EMERGENCY_STOP`                | `0`              | Set to `1` to halt outbound side-effects                  |

See `.env.example` for the full list.

## Tool authentication

Each CLI is responsible for its own auth. Discode does **not** forward the runner's full
environment to tool subprocesses — the environment is scrubbed to an allowlist before each spawn.
A subprocess only ever sees:

- a fixed base set (`PATH`, `HOME`, `LANG`, `TERM`) plus the per-session agent-hook variables, and
- whatever additional keys the tool declares in the `env_passthrough` field of its
  `tool_definitions` row (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).

Secrets the runner holds but a tool does not declare (the bot's `DISCORD_TOKEN`, `DATABASE_URL`,
`REDIS_URL`, `DEPLOYMENT_SECRET_KEY`, and other providers' keys) are **never** passed to the
subprocess. If a tool needs a credential, add its env var to that tool's `env_passthrough`.

| Tool       | Authenticate via                                                            |
| ---------- | --------------------------------------------------------------------------- |
| `claude`   | `claude login` (writes `~/.claude/`) or `ANTHROPIC_API_KEY` in `.env`       |
| `codex`    | `codex login` (writes `~/.codex/`) or `OPENAI_API_KEY` in `.env`            |
| `opencode` | `opencode auth login` (writes per-tool config) or provider keys in `.env`   |
| `droid`    | Set the appropriate API key in `.env` (see the `droid` tool definition row)  |
| custom     | Any tool registered via `/tool register` inherits whatever env vars its `env_passthrough` config declares |

Guild admins can also set a per-session API key override via `/session config set api_key …`;
the value is encrypted at rest with the deployment Fernet key.

Run `uv run python scripts/diag.py` to verify each binary is reachable and its auth works.

## Security model

Discode lets Discord users drive coding agents that execute commands on the runner host. Treat
the **runner host as the trust boundary** and keep these properties in mind when deploying:

- **The agent runs with host privileges.** The seeded `claude` tool runs with
  `--permission-mode bypassPermissions`, i.e. the agent executes file writes and shell commands
  without prompting. Anyone who can send input to a session can run code as the runner OS user
  within the session's `cwd`. Run the runner as a dedicated, least-privileged user (ideally an
  ephemeral/sandboxed one via `SESSION_ISOLATION_MODE`); do not run it as a user that holds
  unrelated production credentials.
- **Subprocess environment is scrubbed** to an allowlist (see *Tool authentication*), so runner
  secrets are not exposed to agents.
- **Working directories are constrained.** `/root add` and alias resolution canonicalize paths
  (resolving symlinks and `..`) and deny credential/system locations — `.ssh`, `.aws`, `.gnupg`,
  `.docker`, `.kube`, `.gcloud`, `.azure`, `.npm`, `.local`, `.config`, `/etc`, `/proc`, `/sys`,
  `/dev`, `/root`, `/boot`, and credential files (`.netrc`, `.npmrc`, `.git-credentials`,
  `.pgpass`, `.env*`). The runner re-validates the `cwd` on create and on resume/recover.
- **Outbound text is redacted.** Replies and notices are passed through a redactor before being
  posted to Discord, scrubbing common secret shapes (Discord tokens, `sk-…`/`sk-ant-…` keys, and
  URLs with embedded credentials). Don't rely on this as a primary control — keep secrets out of
  tool output.
- **Admin commands fail closed** until a guild admin role is configured (see *Bot setup*).
- **Control API is loopback-only** (`127.0.0.1:8788`). Set `RUNNER_CONTROL_AUTH` to require a
  bearer token; on a shared/multi-tenant host, set it even on loopback.
- **Infrastructure auth is your responsibility in production.** The bundled `docker-compose.yml`
  is local-dev only: it binds Postgres/Redis to `127.0.0.1` and uses default/no credentials. For
  any non-laptop deployment, set real Postgres credentials and a Redis password (`requirepass`),
  and never expose those ports publicly.

## Development

```bash
just            # list every recipe, grouped
just lint       # ruff check
just typecheck  # mypy --strict
just test       # pytest — extra args pass through: just test tests/bot_sessions -k resume
just check      # lint + typecheck + test
just audit      # pip-audit the locked dependency tree
```

The `scripts/diag.py` helper exercises the full pipeline from outside Discord — useful when
something replies-but-not-quite-right and you want to bisect between "tool problem" and
"discode problem".

## Operations

- [Discord server setup](docs/operations/discord-setup.md)
- [Incident playbook](docs/operations/incident-playbook.md)

## License

See [LICENSE](LICENSE) (or add one before publishing).
