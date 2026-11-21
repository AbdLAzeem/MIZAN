"""Gemini provider for the agent, using the google-genai SDK.

The client is injected so tests can use a fake. The API key is read from the environment by
`from_env` and is never logged or stored anywhere else.
"""

import json
import os
from collections.abc import Sequence
from typing import Any

from google import genai
from google.genai import types

from mizan.agent.types import AgentTurn, ChatMessage, ToolCall, ToolSpec

DEFAULT_MODEL = "gemini-3.1-flash-lite"


def _tool_name_by_id(messages: Sequence[ChatMessage]) -> dict[str, str]:
    return {c.id: c.name for m in messages for c in m.tool_calls}


def to_contents(messages: Sequence[ChatMessage]) -> tuple[str | None, list[types.Content]]:
    """Convert chat messages to a system instruction and Gemini contents."""
    names = _tool_name_by_id(messages)
    system: str | None = None
    contents: list[types.Content] = []
    pending_responses: list[types.Part] = []

    def flush() -> None:
        if pending_responses:
            contents.append(types.Content(role="user", parts=list(pending_responses)))
            pending_responses.clear()

    for m in messages:
        if m.role == "system":
            system = m.content
        elif m.role == "tool":
            name = names.get(m.tool_call_id or "", "unknown")
            pending_responses.append(
                types.Part.from_function_response(name=name, response=json.loads(m.content))
            )
        else:
            flush()
            if m.role == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=m.content)]))
            else:
                parts: list[types.Part] = []
                if m.content:
                    parts.append(types.Part(text=m.content))
                parts.extend(
                    types.Part(
                        function_call=types.FunctionCall(name=c.name, args=c.arguments),
                        thought_signature=c.provider_data,
                    )
                    for c in m.tool_calls
                )
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
    flush()
    return system, contents


def to_tools(specs: Sequence[ToolSpec]) -> list[Any]:
    declarations = [
        types.FunctionDeclaration(
            name=s.name, description=s.description, parameters_json_schema=s.parameters
        )
        for s in specs
    ]
    return [types.Tool(function_declarations=declarations)] if declarations else []


class GeminiLLM:
    """An `AgentLLM` backed by the Gemini API."""

    def __init__(self, client: Any, model: str = DEFAULT_MODEL, temperature: float = 0.2) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature

    @classmethod
    def from_env(cls) -> "GeminiLLM":
        key = os.environ.get("GEMINI_API_KEY", "").strip().strip("\"'")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        return cls(genai.Client(api_key=key), model)

    def turn(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> AgentTurn:
        system, contents = to_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=to_tools(tools),
            temperature=self._temperature,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = self._client.models.generate_content(
            model=self._model, contents=contents, config=config
        )
        candidates = resp.candidates or []
        parts = (candidates[0].content.parts or []) if candidates and candidates[0].content else []
        text = "".join(p.text for p in parts if getattr(p, "text", None) and not p.thought)
        calls = tuple(
            ToolCall(
                p.function_call.id or f"call_{i}",
                p.function_call.name or "",
                dict(p.function_call.args or {}),
                getattr(p, "thought_signature", None),
            )
            for i, p in enumerate(parts)
            if p.function_call is not None
        )
        usage = getattr(resp, "usage_metadata", None)
        return AgentTurn(
            text=text or None,
            tool_calls=calls,
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
        )
