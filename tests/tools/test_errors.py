from discode.tools.errors import BadToolDefinition, UnknownTool


def test_unknown_tool_message():
    e = UnknownTool("foo")
    assert "foo" in str(e)


def test_bad_tool_definition_includes_field():
    e = BadToolDefinition("droid", "argv_suffix missing {prompt}")
    assert "droid" in str(e)
    assert "argv_suffix" in str(e)
