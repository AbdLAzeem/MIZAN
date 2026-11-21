"""Core agent types and the scripted LLM used in tests."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Opaque provider data that must be sent back unchanged (Gemini thought signature).
    provider_data: bytes | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    """What the model sees about a tool: a name, when to use it, and a JSON schema."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class AgentTurn:
    """One model turn: either final text, or tool calls to run, or both."""

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0


class AgentLLM(Protocol):
    def turn(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> AgentTurn:
        """Return the next assistant turn for the conversation."""
        ...


@dataclass
class ScriptedLLM:
    """A scripted model for tests. Turns are returned in order and every call is recorded."""

    turns: list[AgentTurn]
    calls: list[tuple[tuple[ChatMessage, ...], tuple[ToolSpec, ...]]] = field(default_factory=list)

    def turn(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> AgentTurn:
        self.calls.append((tuple(messages), tuple(tools)))
        if not self.turns:
            raise RuntimeError("ScriptedLLM has no scripted turns left")
        return self.turns.pop(0)
