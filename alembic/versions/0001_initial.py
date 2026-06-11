"""initial schema

Single source of truth: SQLAlchemy ORM models under ``src/discode/db/models/``.
This migration delegates table creation to ``Base.metadata.create_all()``.
Tool seed rows are loaded from ``alembic/seeds/tool_definitions.json``.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-04
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


_SEEDS_PATH = Path(__file__).parent.parent / "seeds" / "tool_definitions.json"


def _load_tool_seeds() -> list[dict]:
    with _SEEDS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def upgrade() -> None:
    # Importing the package registers every table on Base.metadata.
    import discode.db.models  # noqa: F401
    from discode.db.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

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
        bind.execute(
            insert_stmt,
            {
                **seed,
                "argv_prefix": json.dumps(seed["argv_prefix"]),
                "argv_resume_tokens": json.dumps(seed["argv_resume_tokens"]),
                "argv_suffix": json.dumps(seed["argv_suffix"]),
                "extra_paths": json.dumps(seed["extra_paths"]),
                "env_passthrough": json.dumps(seed["env_passthrough"]),
            },
        )


def downgrade() -> None:
    import discode.db.models  # noqa: F401
    from discode.db.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
