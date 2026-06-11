# Discord server setup guide

Step-by-step instructions for a Discord server admin to configure Discode in a guild.

## Prerequisites

### Bot permissions

The bot application must be invited to the server with the following OAuth2 scopes and permissions:

| Permission | Why it is needed |
|------------|-----------------|
| `bot` + `applications.commands` scopes | Allows the bot to join the server and register slash commands |
| **Send Messages** | Post session output and ephemeral replies in channels |
| **Read Message History** | Read thread context in session threads |
| **Manage Threads** | Create public threads for new sessions |
| **Add Reactions** | React with ✅ to acknowledge thread input messages |
| **View Channel** | See channels where threads will be created |

If the bot is missing **Manage Threads** in the target channel, `/start` will fail at thread creation and return: _"Failed to create a Discord thread. Check bot permissions in the mapped channel and try again."_

Permissions can be set per-channel in **Channel Settings → Permissions → @BotName**.

### Bot intents

The bot requires the following privileged gateway intents (enable in the Discord Developer Portal under your application → Bot):

- **Server Members Intent** — needed for membership checks on thread input
- **Message Content Intent** — needed if `ENABLE_MESSAGE_CONTENT_INPUT=true`

## Setup sequence

Complete these steps in order the first time you configure a guild.

### Step 1: Initialize the guild

```
/setup init
```

Registers the guild in the Discode database. You must have the **Manage Server** Discord permission to run this for the first time. The command is idempotent — re-running it is safe.

Expected response: _"Guild initialized."_ (or similar confirmation, ephemeral).

### Step 2: Set the admin role

```
/setup admin-role set @YourAdminRole
```

Replace `@YourAdminRole` with the Discord role whose members should be allowed to run Discode admin commands (`/setup map`, `/setup sync`, etc.).

**Do this before Step 3.** Admin authorization fails closed: until an admin role is configured,
the only admin commands that work are `/setup init` and this `/setup admin-role set`, and both
require the **Manage Server** Discord permission. `/setup map`, `/setup sync`, `/tool …`,
`/root alias …`, and `/session config …` are all denied until the admin role exists. A user with
**Manage Server** can always overwrite the admin role later (bootstrap override).

Verify:

```
/setup admin-role list
```

Expected: _"Admin role: @YourAdminRole"_

### Step 3: Map tools to channels

Each tool must be mapped to a text channel before sessions can be started. Run one `/setup map set` per tool:

```
/setup map set claude   #coding-claude
/setup map set codex    #coding-codex
```

The `tool` parameter must match the string passed to `/start` exactly (case-sensitive).

Verify all mappings:

```
/setup map list
```

Expected output lists each `tool → #channel` pair.

### Step 4: Sync slash commands

```
/setup sync
```

Forces Discord to re-register all slash commands in the correct scopes (global stable commands + guild-scoped setup commands). The bot also auto-syncs on startup, so manual `/setup sync` is rarely needed — run it only after an unexpected drift or when Discord shows stale commands.

The response reports how many commands were added or removed in global and guild scopes.

### Step 5: Start a session (smoke test)

```
/start claude my-test-session /path/to/project
```

Expected: an ephemeral reply confirming the session was started, with a link to the created thread in the mapped channel.

Then in any channel, run `/help` to confirm all 30+ slash commands appear in the listing.

## Verifying the setup

| Check | Command | Expected |
|-------|---------|----------|
| Guild registered | `/setup init` (re-run) | Idempotent success |
| Admin role set | `/setup admin-role list` | Shows the configured role |
| Mappings present | `/setup map list` | Lists all tool→channel pairs |
| Commands visible | Type `/` in Discord | `/setup`, `/start`, `/help`, `/root`, `/tool`, `/session`, `/invite`, `/send`, `/stop`, `/archive`, `/resume`, `/restart`, `/diff` appear |
| Thread creation | `/start <tool> <name> /some/path` | Thread created in mapped channel |

## Discovering commands

After setup, any guild member can run `/help` to see every available slash command,
grouped by parent (`/root`, `/tool`, `/session`, `/setup`) with permission tags
(`[admin]`, `[owner+]`, `[member+]`). The output is ephemeral. Use this in place
of the static command reference when introducing new users.

## Common errors and fixes

### "No channel mapped for tool X"

**Full message**: _"No channel mapped for tool `claude`. Run `/setup map set claude #channel` first."_

**Cause**: `/start` was run for a tool that has no channel mapping.

**Fix**:
```
/setup map set claude #your-channel
```

### Auth failures on setup commands

**Symptom**: Running `/setup map set` or `/setup admin-role set` returns an authorization error.

**Checks**:
1. Run `/setup admin-role list` — if it shows "No admin role configured", admin commands fail closed: only `/setup init` and `/setup admin-role set` are available, and both need the **Manage Server** permission. Run `/setup admin-role set` (with Manage Server) before any other admin command.
2. If a role is configured, confirm the invoking user holds that role in **Server Settings → Members**.
3. If the wrong role was set, a user with **Manage Server** Discord permission can still run `/setup admin-role set` to overwrite it (bootstrap override).

### Bot missing permissions on a channel

**Symptom**: `/start` fails with _"Failed to create a Discord thread."_

**Fix**:
1. Open the target channel → **Edit Channel** → **Permissions**.
2. Add the bot's role (or `@Discode`) with permissions: **View Channel**, **Send Messages**, **Manage Threads**.
3. Retry `/start`.

### Commands not appearing or duplicated after restart

See the [incident playbook — sync drift](incident-playbook.md#sync-drift).

### Bot not responding at all

1. Verify the bot process is running: `make bot` or check `overmind status`.
2. Confirm `DISCORD_TOKEN` in `.env` is valid and the bot is invited to the server.
3. Check bot logs for connection errors.
