"""Durable turns, lifecycle fencing, and retention indexes."""

import sqlalchemy as sa

from alembic import op

revision = "0002_durable_turns"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions", sa.Column("lifecycle_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "sessions",
        sa.Column(
            "lifecycle_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "input_turns",
        sa.Column(
            "sequence", sa.BigInteger(), sa.Identity(always=True), unique=True, nullable=False
        ),
        sa.Column("idempotency_key", sa.Text(), primary_key=True),
        sa.Column(
            "session_id",
            sa.UUID(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lifecycle_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result_text", sa.Text()),
        sa.Column("resume_token", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('queued','executing','completed','delivered','uncertain')",
            name="input_turns_status_check",
        ),
    )
    op.create_index("input_turns_session_created_idx", "input_turns", ["session_id", "created_at"])
    op.create_index(
        "events_session_type_created_idx", "events", ["session_id", "type", "created_at"]
    )
    op.create_index(
        "input_turns_delivered_retention_idx",
        "input_turns",
        ["updated_at"],
        postgresql_where=sa.text("status='delivered'"),
    )
    op.create_index("events_created_at_idx", "events", ["created_at"])
    op.create_index("idempotency_expires_at_idx", "idempotency_keys", ["expires_at"])
    op.create_index(
        "outbox_retention_idx",
        "redis_outbox",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("outbox_retention_idx", "redis_outbox")
    op.drop_index("idempotency_expires_at_idx", "idempotency_keys")
    op.drop_index("events_session_type_created_idx", "events")
    op.drop_index("events_created_at_idx", "events")
    op.drop_table("input_turns")
    op.drop_column("sessions", "lifecycle_changed_at")
    op.drop_column("sessions", "lifecycle_version")
