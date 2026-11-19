"""The LLM boundary.

The rest of the code depends on the `LLM` protocol only, so providers can be swapped,
chained with fallbacks, or replaced by `FakeLLM` in tests. The LLM never computes numbers.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    role: str  # "system", "user", "assistant" or "tool"
    content: str


class LLM(Protocol):
    def complete(self, messages: Sequence[Message]) -> str:
        """Return the assistant reply for the given conversation."""
        ...


class FakeLLM:
    """A scripted LLM for tests. Replies are returned in order and every call is recorded."""

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def complete(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if not self._replies:
            raise RuntimeError("FakeLLM has no scripted replies left")
        return self._replies.pop(0)
