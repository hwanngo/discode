"""initial schema

Frozen initial schema: independent of later ORM changes.
Historical tool seed rows are frozen in this revision.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-04
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


_TOOL_SEEDS = [
    {
        "name": "claude",
        "display_name": "Claude Code",
        "argv_prefix": [
            "claude",
            "--permission-mode",
            "bypassPermissions",
            "--print",
            "--output-format",
            "json",
        ],
        "argv_resume_tokens": ["--resume", "{token}"],
        "argv_suffix": ["{prompt}"],
        "uses_pty": False,
        "resume_token_source": "json_field",
        "resume_token_pattern": "session_id",
        "reply_extractor": "json_field",
        "reply_json_field": "result",
        "extra_paths": [],
        "env_passthrough": ["CLAUDE_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR", "HOME"],
        "model_flag": "--model",
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url_env": "ANTHROPIC_BASE_URL",
        "enabled": True,
    },
    {
        "name": "codex",
        "display_name": "OpenAI Codex",
        "argv_prefix": ["codex", "exec"],
        "argv_resume_tokens": ["resume", "{token}"],
        "argv_suffix": ["{prompt}"],
        "uses_pty": True,
        "resume_token_source": "stderr_regex",
        "resume_token_pattern": "session id:\\s*([0-9a-zA-Z-]+)",
        "reply_extractor": "stdout_strip_ansi",
        "reply_json_field": None,
        "extra_paths": [],
        "env_passthrough": ["OPENAI_API_KEY", "HOME", "CODEX_HOME"],
        "model_flag": "--model",
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "enabled": True,
    },
    {
        "name": "opencode",
        "display_name": "OpenCode",
        "argv_prefix": ["opencode", "run"],
        "argv_resume_tokens": ["--continue"],
        "argv_suffix": ["{prompt}"],
        "uses_pty": True,
        "resume_token_source": "sentinel",
        "resume_token_pattern": "opencode",
        "reply_extractor": "stdout_strip_ansi",
        "reply_json_field": None,
        "extra_paths": [],
        "env_passthrough": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HOME"],
        "model_flag": "--model",
        "api_key_env": None,
        "base_url_env": None,
        "enabled": True,
    },
    {
        "name": "droid",
        "display_name": "Droid (Factory.ai)",
        "argv_prefix": ["droid", "exec", "--output-format", "json"],
        "argv_resume_tokens": ["-s", "{token}"],
        "argv_suffix": ["{prompt}"],
        "uses_pty": False,
        "resume_token_source": "json_field",
        "resume_token_pattern": "session_id",
        "reply_extractor": "json_field",
        "reply_json_field": "result",
        "extra_paths": [],
        "env_passthrough": ["FACTORY_API_KEY", "HOME"],
        "model_flag": "--model",
        "api_key_env": "FACTORY_API_KEY",
        "base_url_env": None,
        "enabled": True,
    },
    {
        "name": "pi",
        "display_name": "Pi (pi.dev)",
        "argv_prefix": ["pi", "-p"],
        "argv_resume_tokens": ["--session-id", "{token}"],
        "argv_suffix": ["{prompt}"],
        "uses_pty": False,
        "resume_token_source": "caller_uuid",
        "resume_token_pattern": None,
        "reply_extractor": "stdout_strip_ansi",
        "reply_json_field": None,
        "extra_paths": [],
        "env_passthrough": [
            "HOME",
            "PI_CODING_AGENT_DIR",
            "GEMINI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
        ],
        "model_flag": "--model",
        "api_key_env": "GEMINI_API_KEY",
        "base_url_env": None,
        "enabled": False,
    },
]


def _load_tool_seeds() -> list[dict]:
    return _TOOL_SEEDS


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _SCHEMA_DDL:
        op.execute(statement)

    # Seed tool_definitions
    insert_stmt = sa.text(
        """
        INSERT INTO tool_definitions (
            name, display_name, argv_prefix, argv_resume_tokens, argv_suffix,
            uses_pty, resume_token_source, resume_token_pattern,
            reply_extractor, reply_json_field, extra_paths, env_passthrough,
            model_flag, api_key_env, base_url_env, enabled, created_by
        ) VALUES (
            :name, :display_name, CAST(:argv_prefix AS JSON),
            CAST(:argv_resume_tokens AS JSON), CAST(:argv_suffix AS JSON),
            :uses_pty, :resume_token_source, :resume_token_pattern,
            :reply_extractor, :reply_json_field,
            CAST(:extra_paths AS JSON), CAST(:env_passthrough AS JSON),
            :model_flag, :api_key_env, :base_url_env, :enabled, 'system'
        )
        """
    )
    for seed in _load_tool_seeds():
        values = {**seed}
        for key in (
            "argv_prefix",
            "argv_resume_tokens",
            "argv_suffix",
            "extra_paths",
            "env_passthrough",
        ):
            values[key] = json.dumps(seed[key])
        bound = insert_stmt.bindparams(
            *(
                sa.bindparam(
                    key, value, type_=sa.Boolean() if isinstance(value, bool) else sa.Text()
                )
                for key, value in values.items()
            )
        )
        bind.execute(bound)


def downgrade() -> None:
    op.drop_table("session_members")
    op.drop_table("session_config")
    op.drop_table("idempotency_keys")
    op.drop_table("events")
    op.drop_table("sessions")
    op.drop_table("path_aliases")
    op.drop_table("guild_settings")
    op.drop_table("allowed_roots")
    op.drop_table("users")
    op.drop_table("tool_definitions")
    op.drop_table("runner_hosts")
    op.drop_table("redis_outbox")
    op.drop_table("guilds")
    op.drop_table("guild_tool_channel_map")
    op.drop_table("guild_admin_role_policy")


# Historical DDL intentionally frozen; evolve through forward revisions.
_SCHEMA_DDL = [
    """
CREATE TABLE guild_admin_role_policy (
	id SERIAL NOT NULL,
	guild_id TEXT NOT NULL,
	role_id TEXT,
	role_name_fallback TEXT,
	updated_by TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT guild_admin_role_policy_guild_id_uq UNIQUE (guild_id)
)

""",
    """
CREATE TABLE guild_tool_channel_map (
	id SERIAL NOT NULL,
	guild_id TEXT NOT NULL,
	tool TEXT NOT NULL,
	channel_id TEXT NOT NULL,
	updated_by TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT guild_tool_channel_map_guild_tool_uq UNIQUE (guild_id, tool)
)

""",
    """
CREATE TABLE guilds (
	id TEXT NOT NULL,
	status TEXT DEFAULT 'active' NOT NULL,
	name TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	CONSTRAINT guilds_status_check CHECK (status IN ('active','inactive','draining','blocked'))
)

""",
    """
CREATE TABLE redis_outbox (
	id BIGSERIAL NOT NULL,
	producer TEXT NOT NULL,
	envelope_type TEXT NOT NULL,
	stream_key TEXT NOT NULL,
	payload JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	attempts INTEGER DEFAULT '0' NOT NULL,
	next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	error TEXT,
	PRIMARY KEY (id)
)

""",
    """
CREATE TABLE runner_hosts (
	id TEXT NOT NULL,
	status TEXT DEFAULT 'unknown' NOT NULL,
	last_heartbeat_at TIMESTAMP WITH TIME ZONE,
	metadata JSON DEFAULT '{}' NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT runner_hosts_id_format_check CHECK (id ~ '^[a-z0-9-]{1,40}$'),
	CONSTRAINT runner_hosts_status_check CHECK (status IN ('online','offline','draining','unknown'))
)

""",
    """
CREATE TABLE tool_definitions (
	name VARCHAR(64) NOT NULL,
	display_name VARCHAR(128) NOT NULL,
	argv_prefix JSON NOT NULL,
	argv_resume_tokens JSON NOT NULL,
	argv_suffix JSON NOT NULL,
	uses_pty BOOLEAN NOT NULL,
	resume_token_source VARCHAR(16) NOT NULL,
	resume_token_pattern VARCHAR(512),
	reply_extractor VARCHAR(32) NOT NULL,
	reply_json_field VARCHAR(64),
	extra_paths JSON NOT NULL,
	env_passthrough JSON NOT NULL,
	model_flag VARCHAR(64),
	api_key_env VARCHAR(64),
	base_url_env VARCHAR(64),
	enabled BOOLEAN NOT NULL,
	created_by VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (name),
	CONSTRAINT tool_definitions_name_format CHECK (name ~ '^[a-z][a-z0-9_-]{0,63}$')
)

""",
    """
CREATE TABLE users (
	id TEXT NOT NULL,
	username TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id)
)

""",
    """
CREATE TABLE allowed_roots (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	guild_id TEXT,
	user_id TEXT DEFAULT '' NOT NULL,
	runner_host_id TEXT NOT NULL,
	path TEXT NOT NULL,
	realpath TEXT NOT NULL,
	created_by TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT allowed_roots_realpath_check CHECK (realpath LIKE '/%'),
	CONSTRAINT allowed_roots_uq UNIQUE (guild_id, user_id, runner_host_id, realpath),
	FOREIGN KEY(guild_id) REFERENCES guilds (id) ON DELETE CASCADE
)

""",
    """
CREATE TABLE guild_settings (
	guild_id TEXT NOT NULL,
	settings JSON DEFAULT '{}' NOT NULL,
	PRIMARY KEY (guild_id),
	FOREIGN KEY(guild_id) REFERENCES guilds (id)
)

""",
    """
CREATE TABLE path_aliases (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	guild_id TEXT NOT NULL,
	alias TEXT NOT NULL,
	path TEXT NOT NULL,
	realpath TEXT NOT NULL,
	created_by TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT path_aliases_uq UNIQUE (guild_id, alias),
	CONSTRAINT path_aliases_alias_check CHECK (alias ~ '^[a-z][a-z0-9_-]{0,63}$'),
	CONSTRAINT path_aliases_realpath_check CHECK (realpath LIKE '/%'),
	FOREIGN KEY(guild_id) REFERENCES guilds (id) ON DELETE CASCADE
)

""",
    """
CREATE TABLE sessions (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	guild_id TEXT,
	owner_id TEXT,
	tool TEXT NOT NULL,
	name TEXT NOT NULL,
	cwd TEXT NOT NULL,
	cwd_realpath TEXT NOT NULL,
	parent_channel_id TEXT,
	thread_id TEXT,
	host_id TEXT,
	hook_secret_hash TEXT NOT NULL,
	hook_secret_ciphertext TEXT,
	shim_token_hash TEXT,
	tool_resume_token TEXT,
	status TEXT NOT NULL,
	status_reason TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	running_at TIMESTAMP WITH TIME ZONE,
	recovery_checked_at TIMESTAMP WITH TIME ZONE,
	last_activity_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	stopped_at TIMESTAMP WITH TIME ZONE,
	archived_at TIMESTAMP WITH TIME ZONE,
	discord_thread_archived_at TIMESTAMP WITH TIME ZONE,
	thread_archive_requested_at TIMESTAMP WITH TIME ZONE,
	thread_archive_dead_lettered_at TIMESTAMP WITH TIME ZONE,
	transcript_bytes_used BIGINT DEFAULT '0' NOT NULL,
	repair_attempts INTEGER DEFAULT '0' NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT sessions_status_check CHECK (status IN ('creating','running','idle','archived','resuming','stopping','stopped','failed','orphaned')),
	CONSTRAINT sessions_archive_deadletter_check CHECK ((thread_archive_dead_lettered_at IS NULL) OR (thread_archive_requested_at IS NOT NULL)),
	FOREIGN KEY(guild_id) REFERENCES guilds (id) ON DELETE CASCADE,
	FOREIGN KEY(owner_id) REFERENCES users (id),
	UNIQUE (thread_id),
	FOREIGN KEY(host_id) REFERENCES runner_hosts (id)
)

""",
    """
CREATE TABLE events (
	id BIGSERIAL NOT NULL,
	idempotency_key TEXT,
	correlation_id TEXT,
	guild_id TEXT,
	session_id UUID,
	actor_id TEXT,
	tool TEXT,
	cwd_realpath TEXT,
	type TEXT NOT NULL,
	payload JSON DEFAULT '{}' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (idempotency_key),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE SET NULL
)

""",
    """
CREATE TABLE idempotency_keys (
	envelope_type TEXT NOT NULL,
	idempotency_key TEXT NOT NULL,
	session_id UUID,
	first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	outcome TEXT,
	claim_expires_at TIMESTAMP WITH TIME ZONE,
	claim_owner TEXT,
	PRIMARY KEY (envelope_type, idempotency_key),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)

""",
    """
CREATE TABLE session_config (
	session_id UUID NOT NULL,
	key VARCHAR(32) NOT NULL,
	value TEXT,
	value_ciphertext TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (session_id, key),
	CONSTRAINT session_config_one_value CHECK ((value IS NOT NULL) <> (value_ciphertext IS NOT NULL)),
	CONSTRAINT session_config_key_format CHECK (key ~ '^[a-z][a-z0-9_]{0,31}$'),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)

""",
    """
CREATE TABLE session_members (
	session_id UUID NOT NULL,
	user_id TEXT NOT NULL,
	role TEXT,
	PRIMARY KEY (session_id, user_id),
	CONSTRAINT session_members_role_check CHECK (role IN ('owner','operator','viewer')),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (id)
)

""",
    """CREATE INDEX redis_outbox_pending_idx ON redis_outbox (producer, published_at, next_attempt_at) WHERE published_at IS NULL""",
    """CREATE INDEX sessions_archive_pending_idx ON sessions (id) WHERE thread_archive_requested_at IS NOT NULL AND discord_thread_archived_at IS NULL""",
    """CREATE INDEX sessions_guild_status_idx ON sessions (guild_id, status)""",
    """CREATE INDEX sessions_host_status_idx ON sessions (host_id, status)""",
    """CREATE INDEX sessions_owner_idx ON sessions (owner_id)""",
]
