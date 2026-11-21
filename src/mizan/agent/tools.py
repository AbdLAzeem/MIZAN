import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mizan.agent.knowledge import Passage, search
from mizan.agent.types import ToolCall, ToolSpec
from mizan.db import Connection
from mizan.engine.gap import compute_gap as engine_compute_gap
from mizan.engine.gap import current_weights, drift_alerts
from mizan.engine.planner import Mode, Side
from mizan.engine.types import AssetClass
from mizan.services.drafts import (
    AccountNotFound,
    DraftNotFound,
    DraftNotOpen,
    EmptyPlan,
    ServiceError,
    load_portfolio,
    prepare_draft,
)
from mizan.services.drafts import (
    cancel_draft as service_cancel_draft,
)

TWOPLACES = Decimal("0.01")


def _format_decimal(val: Decimal) -> str:
    return str(val.quantize(TWOPLACES, rounding=ROUND_HALF_UP))


def _format_price(val: Decimal) -> str:
    s = str(val)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _pascal_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


@dataclass(frozen=True)
class ToolContext:
    conn: Connection
    user_id: uuid.UUID
    account_id: uuid.UUID
    kb: Sequence[Passage] = ()
    max_draft_orders: int = 10
    max_draft_value: Decimal = Decimal("50000")


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    ok: bool
    error: str | None
    ui: dict[str, Any] | None


class GetPortfolioArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetTargetAllocationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComputeGapArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PrepareOrdersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Mode


class CancelDraftArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: uuid.UUID


class SearchKnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)


def tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="get_portfolio",
            description=(
                "Use this tool to view the user's current portfolio holdings, cash balance, "
                "total valuation, and current asset class weights. Do NOT use this tool when you only "
                "need target allocation weights or knowledge base information."
            ),
            parameters=GetPortfolioArgs.model_json_schema(),
        ),
        ToolSpec(
            name="get_target_allocation",
            description=(
                "Use this tool to inspect the user's target allocation weights, risk score, "
                "goal, and investment horizon. Do NOT use this tool when you need current holdings, "
                "drift gaps, or to draft orders."
            ),
            parameters=GetTargetAllocationArgs.model_json_schema(),
        ),
        ToolSpec(
            name="compute_gap",
            description=(
                "Use this tool to compute the drift between the current portfolio weights and target "
                "allocation weights and inspect any active drift alerts. Do NOT use this tool if you "
                "only need raw portfolio balances or target profile settings alone."
            ),
            parameters=ComputeGapArgs.model_json_schema(),
        ),
        ToolSpec(
            name="prepare_orders",
            description=(
                "Use this tool to prepare and stage an order draft for portfolio rebalancing or cash flow "
                "allocation. Do NOT use this tool to confirm, execute, or cancel orders."
            ),
            parameters=PrepareOrdersArgs.model_json_schema(),
        ),
        ToolSpec(
            name="cancel_draft",
            description=(
                "Use this tool to cancel an open order draft by its ID. Do NOT use this tool on drafts "
                "that have already been cancelled or executed."
            ),
            parameters=CancelDraftArgs.model_json_schema(),
        ),
        ToolSpec(
            name="search_knowledge",
            description=(
                "Use this tool to search educational financial articles and knowledge base passages for "
                "relevant concepts. Do NOT use this tool to fetch user account balances, portfolio holdings, "
                "or order drafts."
            ),
            parameters=SearchKnowledgeArgs.model_json_schema(),
        ),
    ]


def _make_success_outcome(data: dict[str, Any], ui: dict[str, Any] | None = None) -> ToolOutcome:
    content = json.dumps({"ok": True, "data": data}, sort_keys=True)
    return ToolOutcome(content=content, ok=True, error=None, ui=ui)


def _make_error_outcome(code: str) -> ToolOutcome:
    content = json.dumps({"ok": False, "error": code}, sort_keys=True)
    return ToolOutcome(content=content, ok=False, error=code, ui=None)


def _handle_get_portfolio(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    GetPortfolioArgs.model_validate(args)
    portfolio = load_portfolio(ctx.conn, ctx.user_id, ctx.account_id)
    weights = current_weights(portfolio)

    sorted_symbols = sorted(portfolio.holdings.keys())
    holdings: list[dict[str, Any]] = []
    for sym in sorted_symbols:
        units = portfolio.holdings[sym]
        inst = portfolio.instruments[sym]
        val = inst.price * Decimal(units)
        holdings.append(
            {
                "symbol": sym,
                "units": units,
                "price": _format_price(inst.price),
                "value": _format_decimal(val),
            }
        )

    data = {
        "cash": _format_decimal(portfolio.cash),
        "holdings_value": _format_decimal(portfolio.holdings_value()),
        "total_value": _format_decimal(portfolio.total_value()),
        "holdings": holdings,
        "weights": {ac.value: _format_decimal(weights[ac]) for ac in AssetClass},
    }
    return _make_success_outcome(data)


def _handle_get_target_allocation(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    GetTargetAllocationArgs.model_validate(args)
    with ctx.conn.transaction():
        acc = ctx.conn.execute(
            "SELECT 1 FROM accounts WHERE id = %s AND user_id = %s",
            (ctx.account_id, ctx.user_id),
        ).fetchone()
        if acc is None:
            raise AccountNotFound("Account not found or not owned by user")

        tp = ctx.conn.execute(
            "SELECT goal, risk, horizon_years, weights FROM target_profiles WHERE account_id = %s",
            (ctx.account_id,),
        ).fetchone()
        if tp is None:
            raise AccountNotFound("Target profile not found")

    raw_weights = tp["weights"]
    if isinstance(raw_weights, str):
        raw_weights = json.loads(raw_weights)

    data = {
        "goal": tp["goal"],
        "risk": int(tp["risk"]),
        "horizon_years": int(tp["horizon_years"]),
        "weights": {k: _format_decimal(Decimal(str(v))) for k, v in raw_weights.items()},
    }
    return _make_success_outcome(data)


def _handle_compute_gap(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    ComputeGapArgs.model_validate(args)
    portfolio = load_portfolio(ctx.conn, ctx.user_id, ctx.account_id)
    with ctx.conn.transaction():
        tp = ctx.conn.execute(
            "SELECT weights FROM target_profiles WHERE account_id = %s",
            (ctx.account_id,),
        ).fetchone()
        if tp is None:
            raise AccountNotFound("Target profile not found")

    raw_weights = tp["weights"]
    if isinstance(raw_weights, str):
        raw_weights = json.loads(raw_weights)

    target = {AssetClass(k): Decimal(str(v)) for k, v in raw_weights.items()}
    current = current_weights(portfolio)
    gap = engine_compute_gap(current, target)
    alerts = drift_alerts(gap, target)

    data = {
        "current": {ac.value: _format_decimal(current[ac]) for ac in AssetClass},
        "target": {ac.value: _format_decimal(target[ac]) for ac in AssetClass},
        "gap": {ac.value: _format_decimal(gap[ac]) for ac in AssetClass},
        "drift_alerts": [ac.value for ac in alerts],
    }
    return _make_success_outcome(data)


def _handle_prepare_orders(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    validated = PrepareOrdersArgs.model_validate(args)
    draft = prepare_draft(ctx.conn, ctx.user_id, ctx.account_id, validated.mode)

    total_order_value = sum((o.price * Decimal(o.units) for o in draft.orders), Decimal("0"))
    if len(draft.orders) > ctx.max_draft_orders or total_order_value > ctx.max_draft_value:
        service_cancel_draft(ctx.conn, ctx.user_id, ctx.account_id, draft.draft_id)
        return _make_error_outcome("draft_limit_exceeded")

    orders_out: list[dict[str, Any]] = []
    for o in draft.orders:
        orders_out.append(
            {
                "symbol": o.symbol,
                "side": o.side.value if isinstance(o.side, Side) else str(o.side),
                "units": o.units,
                "price": _format_price(o.price),
                "value": _format_decimal(o.price * Decimal(o.units)),
            }
        )

    draft_id_str = str(draft.draft_id)
    expires_at_str = draft.expires_at.isoformat()
    data = {
        "draft_id": draft_id_str,
        "mode": validated.mode.value if isinstance(validated.mode, Mode) else str(validated.mode),
        "expires_at": expires_at_str,
        "order_count": len(draft.orders),
        "cash_after": _format_decimal(draft.cash_after),
        "orders": orders_out,
    }
    ui = {
        "draft_id": draft_id_str,
        "token": draft.token,
        "expires_at": expires_at_str,
    }
    return _make_success_outcome(data, ui=ui)


def _handle_cancel_draft(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    validated = CancelDraftArgs.model_validate(args)
    service_cancel_draft(ctx.conn, ctx.user_id, ctx.account_id, validated.draft_id)
    data = {
        "draft_id": str(validated.draft_id),
        "status": "cancelled",
    }
    return _make_success_outcome(data)


def _handle_search_knowledge(ctx: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    validated = SearchKnowledgeArgs.model_validate(args)
    passages = search(ctx.kb, validated.query)
    data = {
        "untrusted_reference_text": True,
        "passages": [
            {
                "source": p.source,
                "heading": p.heading,
                "text": p.text,
            }
            for p in passages
        ],
    }
    return _make_success_outcome(data)


_HANDLERS = {
    "get_portfolio": _handle_get_portfolio,
    "get_target_allocation": _handle_get_target_allocation,
    "compute_gap": _handle_compute_gap,
    "prepare_orders": _handle_prepare_orders,
    "cancel_draft": _handle_cancel_draft,
    "search_knowledge": _handle_search_knowledge,
}


def execute_tool(ctx: ToolContext, call: ToolCall) -> ToolOutcome:
    handler = _HANDLERS.get(call.name)
    if handler is None:
        return _make_error_outcome("unknown_tool")

    try:
        return handler(ctx, call.arguments)
    except ValidationError:
        return _make_error_outcome("invalid_arguments")
    except AccountNotFound:
        return _make_error_outcome("account_not_found")
    except DraftNotFound:
        return _make_error_outcome("draft_not_found")
    except DraftNotOpen:
        return _make_error_outcome("draft_not_open")
    except EmptyPlan:
        return _make_error_outcome("nothing_to_do")
    except ServiceError as exc:
        code = _pascal_to_snake(exc.__class__.__name__)
        return _make_error_outcome(code)
