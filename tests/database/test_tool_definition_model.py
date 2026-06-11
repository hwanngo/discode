import pytest

from discode.db.models import ToolDefinition


@pytest.mark.asyncio
async def test_tool_definition_round_trip(db_session):
    td = ToolDefinition(
        name="echo",
        display_name="Echo",
        argv_prefix=["echo"],
        argv_resume_tokens=[],
        argv_suffix=["{prompt}"],
        uses_pty=False,
        resume_token_source="none",
        resume_token_pattern=None,
        reply_extractor="stdout_raw",
        reply_json_field=None,
        extra_paths=[],
        env_passthrough=[],
        enabled=True,
        created_by="test",
    )
    db_session.add(td)
    await db_session.flush()
    await db_session.refresh(td)
    assert td.name == "echo"
    assert td.argv_prefix == ["echo"]
    assert td.created_at is not None
