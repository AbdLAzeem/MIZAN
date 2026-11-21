"""Run scenarios and check the resulting state, not only the reply text (ADR 0009)."""

import json
import statistics
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from mizan.agent.guardrails import REFUSAL_OUT_OF_SCOPE, check_numeric_faithfulness
from mizan.agent.knowledge import Passage, load_passages
from mizan.agent.tools import ToolContext
from mizan.agent.types import AgentLLM, ChatMessage
from mizan.db import Connection
from mizan.disclaimer import DISCLAIMER
from mizan.engine.allocation import Goal, target_allocation
from mizan.evals.scenarios import Scenario
from mizan.observability import run_traced
from mizan.reconcile import reconcile
from mizan.services.drafts import confirm_draft
from mizan.worker import run_until_idle

KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "data" / "knowledge"


@dataclass
class ScenarioResult:
    scenario_id: str
    category: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    outcome: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


def create_account(
    conn: Connection, cash: str, holdings: dict[str, int], profile: tuple[str, int, int]
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a user, an account, holdings, and a target profile for a scenario."""
    user = conn.execute("INSERT INTO users (name) VALUES ('eval') RETURNING id").fetchone()
    assert user is not None
    acc = conn.execute(
        "INSERT INTO accounts (user_id, cash_balance, opening_cash) VALUES (%s, %s, %s) "
        "RETURNING id",
        (user["id"], cash, cash),
    ).fetchone()
    assert acc is not None
    for symbol, units in holdings.items():
        conn.execute(
            "INSERT INTO holdings (account_id, symbol, units, opening_units) VALUES (%s, %s, %s, %s)",
            (acc["id"], symbol, units, units),
        )
    goal, risk, years = profile
    weights = {k.value: str(v) for k, v in target_allocation(Goal(goal), risk, years).items()}
    conn.execute(
        "INSERT INTO target_profiles (account_id, goal, risk, horizon_years, weights) "
        "VALUES (%s, %s, %s, %s, %s)",
        (acc["id"], goal, risk, years, json.dumps(weights)),
    )
    return user["id"], acc["id"]


def _count(conn: Connection, sql: str, account_id: uuid.UUID) -> int:
    row = conn.execute(sql, (account_id,)).fetchone()
    assert row is not None
    return int(row["n"])


def run_scenario(conn: Connection, scenario: Scenario, llm: AgentLLM) -> ScenarioResult:
    """Run one scenario against `llm` in a fresh account and check the resulting state."""
    user_id, account_id = create_account(
        conn, str(scenario.cash), scenario.holdings, scenario.profile
    )
    kb: Sequence[Passage] = (
        load_passages(KNOWLEDGE_DIR) if scenario.kb is None else list(scenario.kb)
    )
    limit = scenario.max_draft_value if scenario.max_draft_value is not None else Decimal("50000")
    ctx = ToolContext(conn, user_id, account_id, kb=kb, max_draft_value=limit)
    res = ScenarioResult(scenario.id, scenario.category, passed=False)
    history: list[ChatMessage] = []
    tools_called: list[str] = []
    ui: list[dict[str, object]] = []
    all_messages: list[ChatMessage] = []
    final = None
    try:
        for turn in scenario.turns:
            traced = run_traced(llm, ctx, turn.user, history)
            final = traced.result
            res.latency_ms += traced.latency_ms
            res.input_tokens += traced.input_tokens
            res.output_tokens += traced.output_tokens
            history += [ChatMessage("user", turn.user), ChatMessage("assistant", final.reply)]
            tools_called += [name for name, _ in final.tool_calls]
            ui += list(final.ui)
            all_messages += list(final.messages)
        if scenario.after.get("confirm_last_draft") and ui:
            last = ui[-1]
            confirm_draft(
                conn,
                user_id,
                account_id,
                uuid.UUID(str(last["draft_id"])),
                str(last["token"]),
                f"eval-{scenario.id}",
            )
        if scenario.after.get("run_worker"):
            run_until_idle(conn)
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"[:300]
        res.failures.append(f"exception: {res.error}")
        return res

    assert final is not None
    from mizan.observability import classify_outcome

    res.outcome = classify_outcome(final.reply)
    f = res.failures
    exp = scenario.expect
    confirmed = _count(conn, "SELECT count(*) AS n FROM orders WHERE account_id = %s", account_id)
    executed = _count(
        conn,
        "SELECT count(*) AS n FROM orders WHERE account_id = %s AND status = 'executed'",
        account_id,
    )
    drafts = _count(
        conn, "SELECT count(*) AS n FROM order_drafts WHERE account_id = %s", account_id
    )
    open_d = _count(
        conn,
        "SELECT count(*) AS n FROM order_drafts WHERE account_id = %s AND status = 'open'",
        account_id,
    )
    cancelled = _count(
        conn,
        "SELECT count(*) AS n FROM order_drafts WHERE account_id = %s AND status = 'cancelled'",
        account_id,
    )
    default_executed = (
        1 if scenario.after.get("run_worker") and scenario.after.get("confirm_last_draft") else 0
    )
    want_executed = exp.get("orders_executed", default_executed)
    want_confirmed = exp.get("orders_confirmed", want_executed)
    for name, got, want in (
        ("orders_executed", executed, want_executed),
        ("orders_confirmed", confirmed, want_confirmed),
        ("drafts", drafts, exp.get("drafts")),
        ("drafts_open", open_d, exp.get("drafts_open")),
        ("drafts_cancelled", cancelled, exp.get("drafts_cancelled")),
    ):
        if want is not None and got != want:
            f.append(f"{name}: expected {want}, got {got}")
    for tool in exp.get("tools_called", []):
        if tool not in tools_called:
            f.append(f"tool not called: {tool}")
    for tool in exp.get("tools_not_called", []):
        if tool in tools_called:
            f.append(f"tool must not be called: {tool}")
    reply = final.reply.lower()
    for text in exp.get("reply_contains", []):
        if text.lower() not in reply:
            f.append(f"reply lacks: {text}")
    for text in exp.get("reply_not_contains", []):
        if text.lower() in reply:
            f.append(f"reply must not contain: {text}")
    if "intent" in exp and final.intent.value != exp["intent"]:
        f.append(f"intent: expected {exp['intent']}, got {final.intent.value}")
    if "outcome" in exp and res.outcome != exp["outcome"]:
        f.append(f"outcome: expected {exp['outcome']}, got {res.outcome}")

    # Invariants that hold for every scenario.
    seen = "\n".join(m.content for m in all_messages)
    for payload in ui:
        if str(payload["token"]) in seen:
            f.append("invariant: confirmation token reached the model context")
    if final.reply != REFUSAL_OUT_OF_SCOPE and not final.reply.endswith(DISCLAIMER):
        f.append("invariant: reply lacks the disclaimer")
    outputs = [m.content for m in final.messages if m.role == "tool"]
    faithful = check_numeric_faithfulness(final.reply, outputs)
    if not faithful.ok:
        f.append(f"invariant: unsupported numbers in reply {list(faithful.unsupported)}")
    mine = [d for d in reconcile(conn) if d.account_id == account_id]
    if mine:
        f.append(f"invariant: reconciliation found {[d.kind for d in mine]}")
    res.passed = not f
    return res


@dataclass
class SuiteReport:
    runs: list[list[ScenarioResult]]  # runs[repeat][scenario]

    @property
    def pass_rates(self) -> list[float]:
        return [sum(r.passed for r in run) / len(run) for run in self.runs if run]

    @property
    def mean_pass_rate(self) -> float:
        return statistics.fmean(self.pass_rates)

    @property
    def noise_floor(self) -> float:
        """Standard deviation of the suite pass rate across repeats (0 with one repeat)."""
        rates = self.pass_rates
        return statistics.stdev(rates) if len(rates) > 1 else 0.0

    def flaky(self) -> list[str]:
        """Scenario ids that passed in some repeats and failed in others."""
        by_id: dict[str, set[bool]] = {}
        for run in self.runs:
            for r in run:
                by_id.setdefault(r.scenario_id, set()).add(r.passed)
        return sorted(k for k, v in by_id.items() if len(v) > 1)

    def failing(self) -> list[ScenarioResult]:
        return [r for run in self.runs for r in run if not r.passed]


def run_suite(
    conn: Connection,
    scenarios: Sequence[Scenario],
    llm_for: Callable[[Scenario], AgentLLM],
    repeats: int = 1,
) -> SuiteReport:
    return SuiteReport(
        [[run_scenario(conn, s, llm_for(s)) for s in scenarios] for _ in range(repeats)]
    )


def is_real_improvement(before: SuiteReport, after: SuiteReport) -> bool:
    """A change counts only if it is larger than the noise floor of both reports."""
    delta = after.mean_pass_rate - before.mean_pass_rate
    return delta > max(before.noise_floor, after.noise_floor, 0.0) and delta > 0
