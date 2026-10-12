"""Correct stale tool seeds on databases migrated before the seeds were fixed.

* ``pi``: databases that ran 0001 before commit 24df464 kept the old Inflection
  seed (``resume_token_source='none'``, no session id). Upgrade it in place.
* ``opencode``: its ``sentinel`` resume is rejected at /start and at execution,
  so leaving it enabled only advertises a tool that cannot run.

Only rows still matching the original seed are touched; operator edits win.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0004_tool_seed_fixes"
down_revision = "0003_turn_delivery_failed"
branch_labels = None
depends_on = None

_PI_ENV = [
    "HOME",
    "PI_CODING_AGENT_DIR",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
]


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("""
            UPDATE tool_definitions
            SET display_name = 'Pi (pi.dev)',
                argv_resume_tokens = CAST(:resume AS json),
                resume_token_source = 'caller_uuid',
                env_passthrough = CAST(:env AS json),
                model_flag = '--model',
                api_key_env = 'GEMINI_API_KEY',
                updated_at = now()
            WHERE name = 'pi'
              AND display_name = 'Pi (Inflection)'
              AND resume_token_source = 'none'
        """),
        {"resume": json.dumps(["--session-id", "{token}"]), "env": json.dumps(_PI_ENV)},
    )
    op.execute(
        "UPDATE tool_definitions SET enabled = false, updated_at = now() "
        "WHERE name = 'opencode' AND resume_token_source = 'sentinel' AND enabled"
    )


def downgrade() -> None:
    # Data-only correction; the previous values were broken, so nothing is restored.
    pass
