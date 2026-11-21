"""Tracing of agent runs into PostgreSQL (tables agent_runs and agent_steps)."""

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from psycopg.types.json import Jsonb

from mizan.agent.agent import FALLBACK_INCOMPLETE, AgentResult, run_agent
from mizan.agent.guardrails import FALLBACK_UNVERIFIED, REFUSAL_OUT_OF_SCOPE
from mizan.agent.tools import ToolContext, ToolOutcome
from mizan.agent.types import AgentLLM, AgentTurn, ChatMessage, ToolCall, ToolSpec
from mizan.db import Connection


@dataclass
class StepEvent:
    kind: str  # "model" or "tool"
    name: str
    ok: bool
    latency_ms: int
    detail: dict[str, Any] = field(default_factory=dict)


class TimedLLM:
    """Wraps an `AgentLLM` and records the latency and token usage of every model turn."""

    def __init__(self, inner: AgentLLM, events: list[StepEvent]) -> None:
        self._inner = inner
        self._events = events

    def turn(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> AgentTurn:
        start = time.perf_counter()
        try:
            result = self._inner.turn(messages, tools)
        except Exception as exc:
            ms = round((time.perf_counter() - start) * 1000)
            self._events.append(StepEvent("model", "llm", False, ms, {"error": str(exc)[:500]}))
            raise
        ms = round((time.perf_counter() - start) * 1000)
        detail = {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "tool_calls": [c.name for c in result.tool_calls],
        }
        self._events.append(StepEvent("model", "llm", True, ms, detail))
        return result


def classify_outcome(reply: str) -> str:
    if reply == REFUSAL_OUT_OF_SCOPE:
        return "refused"
    if FALLBACK_UNVERIFIED in reply or FALLBACK_INCOMPLETE in reply:
        return "fallback"
    return "ok"


@dataclass(frozen=True)
class TracedResult:
    result: AgentResult
    run_id: uuid.UUID
    latency_ms: int
    input_tokens: int
    output_tokens: int


def save_run(
    conn: Connection,
    account_id: uuid.UUID,
    user_message: str,
    events: Sequence[StepEvent],
    latency_ms: int,
    result: AgentResult | None,
    error: str | None = None,
) -> uuid.UUID:
    in_tokens = sum(int(e.detail.get("input_tokens", 0)) for e in events)
    out_tokens = sum(int(e.detail.get("output_tokens", 0)) for e in events)
    outcome = "error" if result is None else classify_outcome(result.reply)
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO agent_runs (account_id, user_message, intent, reply, steps, corrected, "
            "outcome, latency_ms, input_tokens, output_tokens, error) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                account_id,
                user_message,
                result.intent.value if result else None,
                result.reply if result else None,
                result.steps if result else 0,
                result.corrected if result else False,
                outcome,
                latency_ms,
                in_tokens,
                out_tokens,
                error,
            ),
        ).fetchone()
        assert row is not None
        run_id: uuid.UUID = row["id"]
        for idx, e in enumerate(events):
            conn.execute(
                "INSERT INTO agent_steps (run_id, idx, kind, name, ok, latency_ms, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (run_id, idx, e.kind, e.name, e.ok, e.latency_ms, Jsonb(e.detail)),
            )
    return run_id


def run_traced(
    llm: AgentLLM,
    ctx: ToolContext,
    user_message: str,
    history: Sequence[ChatMessage] = (),
) -> TracedResult:
    """Run the agent and always store a trace. Errors are stored and then re-raised."""
    events: list[StepEvent] = []

    def on_tool(call: ToolCall, outcome: ToolOutcome, elapsed_ms: int) -> None:
        detail = {"error": outcome.error} if outcome.error else {}
        events.append(StepEvent("tool", call.name, outcome.ok, elapsed_ms, detail))

    start = time.perf_counter()
    try:
        result = run_agent(TimedLLM(llm, events), ctx, user_message, history, on_tool=on_tool)
    except Exception as exc:
        ms = round((time.perf_counter() - start) * 1000)
        save_run(ctx.conn, ctx.account_id, user_message, events, ms, None, error=str(exc)[:500])
        raise
    ms = round((time.perf_counter() - start) * 1000)
    run_id = save_run(ctx.conn, ctx.account_id, user_message, events, ms, result)
    return TracedResult(
        result,
        run_id,
        ms,
        sum(int(e.detail.get("input_tokens", 0)) for e in events),
        sum(int(e.detail.get("output_tokens", 0)) for e in events),
    )
