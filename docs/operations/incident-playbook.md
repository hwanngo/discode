# Incident playbook

Runbook for common operational issues with a running Discode deployment.

---

## Multiple sessions in one cwd

Tools whose resume mode selects the latest conversation by cwd cannot safely share a
workspace: only one active session per tool/cwd is admitted. Explicit-token tools keep
separate conversation identities, but filesystem writes still share the same directory.
Use separate working copies for independent tasks.

---

## Sync drift — commands missing or duplicated after restart

**Symptom**: Slash commands are missing from Discord's command picker, or old/duplicate commands appear, after a bot restart or deployment.

**Cause**: Discord's command cache can lag behind bot restarts. The bot runs `reconcile_commands()` on `on_ready`, but if the startup sync failed (logged as _"command_sync on_ready failed — commands may be stale"_), commands may be out of sync.

**Resolution**:

1. Run:
   ```
   /setup sync
   ```
   This forces a full re-sync of global and guild-scoped commands. The response reports adds/removes in each scope.

2. Wait up to ~60 seconds for Discord's client cache to refresh.

3. If `/setup sync` itself is not visible, the bot's guild-scoped commands failed to register. Check bot logs for the `command_sync on_ready failed` warning and inspect the underlying exception.

**What to check in logs**:
- `command_sync on_ready complete` — normal startup line; check the `global_added`/`guild_added` counts.
- `command_sync on_ready failed — commands may be stale` — startup sync threw an exception. Look at the traceback immediately following.

**Guild vs global scope**: With the default `DISCORD_GUILD_COMMANDS_ONLY=true`, every command is guild-scoped. With `false`, `/setup` and `/tool` stay guild-scoped and the session commands (`/start`, `/help`, `/diff`, `/invite`, `/send`, `/stop`, `/archive`, `/resume`, `/restart`, `/root`, `/session`) are global. Commands are only registered for guilds listed in `ALLOWED_GUILD_IDS`; guilds joined after startup are registered on join. If a guild is missing from `ALLOWED_GUILD_IDS` the bot answers every command with _"Discode is not enabled for this server."_ (and leaves the guild if it was invited after startup).

---

## Mapping missing — `/start` fails with "no channel mapped"

**Symptom**: `/start <tool> <name> <cwd>` returns _"No channel mapped for tool `<tool>`. Run `/setup map set <tool> #channel` first."_

**Cause**: No `guild_tool_channel_map` row exists for `(guild_id, tool)`.

**Resolution**:

1. Run `/setup map list` to see what is currently mapped.

2. Add the missing mapping:
   ```
   /setup map set <tool> #target-channel
   ```

3. Retry `/start`.

**If the mapped channel was deleted**: The bot will return _"Mapped channel `#<id>` is not accessible."_ Re-map the tool to a valid channel:
```
/setup map set <tool> #new-channel
```

---

## Thread bind drift (legacy rows)

`/start` creates the Discord thread first and passes its `thread_id` into the session
transaction, so new sessions are always bound. The janitor still runs
`sweep_session_thread_binding_drift()` as a safety net for rows created by older
versions (`thread_id IS NULL`, or `status_reason = 'thread_bind_failed'`): it re-binds
from the matching `runner.create_session.v1` outbox payload and, after 3 failed
attempts, sets `status_reason = 'thread_bind_repair_exhausted'`.

If a session is stuck there, stop it with `/stop <session>` and start a new one.
To retry the repair instead:

```sql
UPDATE sessions SET repair_attempts = 0, status_reason = 'thread_bind_failed'
WHERE id = '<session_uuid>';
```

---

## Local recovery

Use these steps to fully reset a local dev environment.

### Kill all processes

If `just dev` (overmind) is stuck or processes are orphaned:

```bash
overmind stop
```

Or kill overmind and all children directly:

```bash
pkill -f overmind
pkill -f discode-bot
pkill -f discode-runner
pkill -f discode-dispatcher
pkill -f discode-janitor
```

### No tmux

The runner does not use tmux — each Discord message spawns one short-lived
subprocess directly. If you find tmux references in docs or scripts, they're
historical artifacts. There's no per-session process to "discover" or "kill";
sessions are pure DB state.

To stop a session, prefer `/stop` in Discord. `/stop` both updates the DB row **and** enqueues a
`runner.stop_session.v1` envelope that tells the runner to drop its in-memory `session_store`
entry. A direct `UPDATE sessions SET status = 'stopped' WHERE id = '<uuid>'` updates only the DB —
the runner keeps its in-memory entry until that envelope is processed or the runner restarts, so
use it only when the runner is already down. Resume works the same way: `/resume` moves the
session to `resuming` and enqueues `runner.resume_session.v1`; the runner rehydrates and flips it
to `running`. Setting `status = 'running'` directly will **not** rehydrate the runner.

### Flush Redis (test environments only)

To clear all Redis state in a local test environment:

```bash
redis-cli FLUSHDB
```

> **Warning**: Do not run `FLUSHDB` or `FLUSHALL` in production. It will drop the outbox stream and all pending jobs, causing message loss.

### Re-run database migrations

If the schema is out of date after pulling new code:

```bash
just migrate
```

### Full local reset sequence

```bash
overmind stop          # or pkill as above
docker compose down    # stop Postgres and Redis containers
docker compose up -d postgres redis   # restart fresh (data is NOT wiped unless you remove volumes)
just migrate           # reapply migrations (0001_initial … 0004_tool_seed_fixes)
just dev               # restart all processes
```

To wipe all data (including Postgres volumes):

```bash
docker compose down -v   # removes named volumes
docker compose up -d postgres redis
just migrate
```

## Durable input and failed delivery

The `input_turns` ledger records queued, executing, completed, delivered and uncertain
turns. A completed result can be delivered again without rerunning the agent. An executing
turn discovered after a crash becomes uncertain; inspect workspace changes and provider
history before deciding to restart. Do not delete the ledger to force a replay.

List exhausted outbox metadata: `uv run python scripts/recover_outbox.py`.
After reviewing a particular operation, retry its publication with
`uv run python scripts/recover_outbox.py --retry <row-id>`. This command changes the local
configured database and is an operator action, not a diagnostic. Exhausted rows are retained;
retention only deletes published rows. Pending/unread Redis messages are never trimmed.
Monitor `discode_deadletter_outbox` and `dab_outbox_publish_total` on `/metrics`.

## Emergency stop and supervision

Set `EMERGENCY_STOP=1` and restart all processes to reject new tool execution and outbound
posts and cancel active tool turns.
A dotenv edit does not change already loaded process settings. For immediate containment,
send the runner SIGTERM twice: the first stops new input and starts the
`RUNNER_DRAIN_TIMEOUT_SECONDS` drain, the second kills in-flight tool process groups at once.
Then verify its children exit. Tool processes run in their own sessions, so SIGKILL of the
runner (or `just dev-stop`) leaves them running; kill those explicitly. Do not rely on a
running bot to contain a compromised host.

The Procfile and Compose stack are development tooling, not a production supervisor.
Production operators must configure process restart policies, authenticated database/Redis,
backups and restore exercises, resource limits and health probes. A deployment with no
supervisor, backup/restore verification or dedicated trusted host is not release-ready.

Delivered turn output expires according to `INPUT_IDEMPOTENCY_RETENTION` (30d by default);
execution tombstones remain to prevent old message replay. Undelivered and uncertain output
is retained for explicit recovery. Restrict database access and encrypt backups.

Legacy `SESSION_UID_POOL_*`, `RUNNER_JOB_CLAIM_TIMEOUT_SECONDS`, and
`INPUT_TENTATIVE_LEASE_SECONDS`/`IDEMPOTENCY_KEY_RETENTION_HOURS` are obsolete and ignored. They do not establish sandboxing or govern durable-turn
execution leases. Turn ordering uses database advisory locks and the durable turn ledger.
For macOS Docker contexts, Testcontainers may require
`DOCKER_HOST=$(docker context inspect --format '{{.Endpoints.docker.Host}}') uv run pytest`.
Never substitute a production database for the disposable test fixtures.

Dispatcher failures also remain recoverable in the Redis hash
`discode:deadletter:discord`. List metadata with
`uv run python scripts/recover_dispatcher.py`; replay one reviewed entry with
`uv run python scripts/recover_dispatcher.py --retry <stream-id>`.
Successful chunks have PostgreSQL delivery checkpoints, while a response lost before its
checkpoint may be reposted with the same nonce. Discord can still duplicate that message
outside its nonce window; delivery is at-least-once. Review actual Discord history before
manual replay. The script never prints the retained payload; access to Redis still exposes
that data and must remain restricted. Monitor `discode_dispatcher_deadletters_total`.

Runner jobs that exhaust `RUNNER_JOB_MAX_DELIVERIES` (or have an unparseable payload) are
retained in `discode:deadletter:runner:jobs:<host_id>` and
`discode:deadletter:input:jobs:<host_id>`, keyed by stream id, as JSON
`{"fields", "reason", "attempts"}`. Inspect with `HGETALL`; replay a reviewed entry by
`XADD`ing its `fields` back to the stream and `HDEL`ing it. Monitor
`discode_runner_deadletters_total`.
