from __future__ import annotations


class UnknownTool(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"unknown or disabled tool: {name}")
        self.name = name


class BadToolDefinition(Exception):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"bad tool definition '{name}': {reason}")
        self.name = name
        self.reason = reason


class BadSessionConfig(Exception):
    def __init__(self, session_id: str, reason: str) -> None:
        super().__init__(f"bad session config '{session_id}': {reason}")
        self.session_id = session_id
        self.reason = reason
