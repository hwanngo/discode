"""Terminal delivery_failed/aborted turn statuses; retention and janitor sweep indexes."""

import sqlalchemy as sa

from alembic import op

revision = "0003_turn_delivery_failed"
down_revision = "0002_durable_turns"
branch_labels = None
depends_on = None

_OLD_STATUSES = "status IN ('queued','executing','completed','delivered','uncertain')"
_NEW_STATUSES = (
    "status IN ('queued','executing','completed','delivered','uncertain',"
    "'delivery_failed','aborted')"
)
_RETENTION_WHERE = (
    "status IN ('delivered','delivery_failed') "
    "AND (result_text IS NOT NULL OR resume_token IS NOT NULL)"
)


def upgrade() -> None:
    op.drop_constraint("input_turns_status_check", "input_turns", type_="check")
    op.create_check_constraint("input_turns_status_check", "input_turns", _NEW_STATUSES)
    op.drop_index("input_turns_delivered_retention_idx", "input_turns")
    op.create_index(
        "input_turns_delivered_retention_idx",
        "input_turns",
        ["updated_at"],
        postgresql_where=sa.text(_RETENTION_WHERE),
    )
    # Janitor timeout sweeps filter on status + last_activity_at across all guilds.
    op.create_index("sessions_status_activity_idx", "sessions", ["status", "last_activity_at"])


def downgrade() -> None:
    op.drop_index("sessions_status_activity_idx", "sessions")
    op.drop_index("input_turns_delivered_retention_idx", "input_turns")
    op.create_index(
        "input_turns_delivered_retention_idx",
        "input_turns",
        ["updated_at"],
        postgresql_where=sa.text("status='delivered'"),
    )
    op.execute(
        "UPDATE input_turns SET status='delivered' WHERE status IN ('delivery_failed','aborted')"
    )
    op.drop_constraint("input_turns_status_check", "input_turns", type_="check")
    op.create_check_constraint("input_turns_status_check", "input_turns", _OLD_STATUSES)
