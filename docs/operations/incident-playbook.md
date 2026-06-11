# Incident playbook

Runbook for common operational issues with a running Discode deployment.

---

## Multiple sessions in one cwd

Discode allows multiple concurrent sessions to share a `cwd` — each turn
spawns its own short-lived subprocess, so there's nothing for two sessions to
collide on. The `STRICT_SINGLE_SESSION_PER_CWD` flag and the `cwd_busy` error
no longer exist. If you find documentation referencing them, it's stale.

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

**Guild vs global scope**: Setup commands (`/setup`) are guild-scoped and only appear in guilds where they've been synced. Session lifecycle commands (`/start`, `/help`, `/diff`, `/invite`, `/send`, `/stop`, `/archive`, `/resume`, `/restart`, `/root`, `/tool`, `/session`) are global and appear in all guilds after a global sync. All are implemented as of 2026-05-04.

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

## Thread bind failure — `thread_bind_failed`

**Symptom**: `/start` returns a degraded-success message: _"Session `<id[:8]>` started in `#<channel>` (thread `#<thread>`), but the thread binding encountered an issue and will be repaired automatically."_

**What it means**: The session was created in the database and a Discord thread was created, but the atomic write that binds `thread_id` to the session row (and to the outbox payload) failed. The session has `status_reason = 'thread_bind_failed'` in the `sessions` table.

**Auto-repair by the janitor**:

The janitor runs `sweep_session_thread_binding_drift()` on each cycle. It:
1. Finds sessions with `thread_id IS NULL` and `status IN (pending, running, creating)`, plus any sessions with `status_reason = 'thread_bind_failed'`.
2. Looks for a matching `runner.create_session.v1` outbox row whose payload already contains a `thread_id`.
3. If found, calls `bind_session_thread()` atomically to repair the row.
4. Increments `repair_attempts` on each failed attempt.
5. After `max_attempts` (default: 3) failed attempts, marks the session `status_reason = 'thread_bind_repair_exhausted'` and emits a `WARNING` log.

**What to do if auto-repair does not succeed**:

1. Check janitor logs for `sweep_session_thread_binding_drift` entries. Look for the session ID.

2. If `status_reason = 'thread_bind_repair_exhausted'`, the session is dead-lettered. Inspect the outbox:
   ```sql
   SELECT id, payload, attempts, published_at, error
   FROM redis_outbox
   WHERE payload->>'session_id' = '<session_id>'
     AND envelope_type = 'runner.create_session.v1';
   ```

3. If the outbox row has a `thread_id` in its payload, you can manually reset `repair_attempts` to unblock the janitor:
   ```sql
   UPDATE sessions SET repair_attempts = 0, status_reason = 'thread_bind_failed'
   WHERE id = '<session_uuid>';
   ```
   The janitor will retry on the next sweep.

4. If no outbox row exists for the session, the session cannot be auto-repaired. Stop it manually (via the runner control API or tmux) and start a new one:
   ```
   /start <tool> <name> <cwd>
   ```

---

## Local recovery

Use these steps to fully reset a local dev environment.

### Kill all processes

If `make dev` (overmind) is stuck or processes are orphaned:

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
make migrate
```

### Full local reset sequence

```bash
overmind stop          # or pkill as above
docker compose down    # stop Postgres and Redis containers
docker compose up -d postgres redis   # restart fresh (data is NOT wiped unless you remove volumes)
make migrate           # reapply migrations (single squashed migration: 0001_initial)
make dev               # restart all processes
```

To wipe all data (including Postgres volumes):

```bash
docker compose down -v   # removes named volumes
docker compose up -d postgres redis
make migrate
```
