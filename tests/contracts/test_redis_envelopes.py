from __future__ import annotations

import pytest
from pydantic import ValidationError

from discode.specs.redis_envelopes import (
    ENVELOPE_TYPES,
    CreateSessionEnvelope,
)

EXPECTED_ENVELOPE_TYPES = {
    "runner.create_session.v1",
    "runner.send_input.v1",
    "runner.resume_session.v1",
    "runner.stop_session.v1",
    "discord.output_chunk.v1",
    "discord.system_notice.v1",
    "discord.terminal_notice.v1",
    "discord.archive_thread.v1",
}


def test_envelope_types_contains_exactly_eight_types() -> None:
    """ENVELOPE_TYPES frozenset contains exactly the 8 expected types."""
    assert ENVELOPE_TYPES == EXPECTED_ENVELOPE_TYPES
    assert len(ENVELOPE_TYPES) == 8


def test_create_session_envelope_validates_with_required_fields() -> None:
    """CreateSessionEnvelope validates with required fields."""
    envelope = CreateSessionEnvelope(
        type="runner.create_session.v1",
        idempotency_key="idem-123",
        session_id="session-abc",
        guild_id="guild-xyz",
        owner_id="user-001",
        tool="claude",
        cwd_realpath="/home/user/project",
        thread_id="thread-123",
        hook_secret_ciphertext="secret-cipher",
    )
    assert envelope.type == "runner.create_session.v1"
    assert envelope.model is None
    assert envelope.resume_token is None


def test_extra_field_in_envelope_raises_validation_error() -> None:
    """Extra field not in envelope raises ValidationError."""
    with pytest.raises(ValidationError):
        CreateSessionEnvelope(
            type="runner.create_session.v1",
            idempotency_key="idem-123",
            session_id="session-abc",
            guild_id="guild-xyz",
            owner_id="user-001",
            tool="claude",
            cwd_realpath="/home/user/project",
            thread_id="thread-123",
            hook_secret_ciphertext="secret-cipher",
            nonexistent_field="should_fail",  # type: ignore[call-arg]
        )
