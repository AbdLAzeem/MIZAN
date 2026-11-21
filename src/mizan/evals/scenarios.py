"""Scenario definitions for the golden set, loaded from JSON files."""

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from mizan.agent.knowledge import Passage
from mizan.agent.types import AgentTurn, ToolCall

CATEGORIES = frozenset(
    {"education", "portfolio", "orders", "safety", "injection", "limits", "multi_turn"}
)
EXPECT_KEYS = frozenset(
    {
        "orders_confirmed",
        "orders_executed",
        "drafts",
        "drafts_open",
        "drafts_cancelled",
        "tools_called",
        "tools_not_called",
        "reply_contains",
        "reply_not_contains",
        "intent",
        "outcome",
    }
)
AFTER_KEYS = frozenset({"confirm_last_draft", "run_worker"})


@dataclass(frozen=True)
class Turn:
    user: str
    script: tuple[AgentTurn, ...]  # used only with the scripted (fake) model


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    description: str
    cash: Decimal
    holdings: dict[str, int]
    profile: tuple[str, int, int]
    kb: tuple[Passage, ...] | None  # None means the real knowledge base
    max_draft_value: Decimal | None
    turns: tuple[Turn, ...]
    expect: dict[str, Any]
    after: dict[str, Any]


def _script(items: list[dict[str, Any]], prefix: str) -> tuple[AgentTurn, ...]:
    turns = []
    for i, item in enumerate(items):
        calls = tuple(
            ToolCall(f"{prefix}-{i}-{j}", c["name"], dict(c.get("arguments", {})))
            for j, c in enumerate(item.get("tool_calls", []))
        )
        turns.append(AgentTurn(text=item.get("text"), tool_calls=calls))
    return tuple(turns)


def parse_scenario(raw: dict[str, Any]) -> Scenario:
    """Parse and validate one scenario. Raises ValueError on any schema problem."""
    sid = raw.get("id")
    if not isinstance(sid, str) or not sid:
        raise ValueError("scenario needs a string id")
    if raw.get("category") not in CATEGORIES:
        raise ValueError(f"{sid}: category must be one of {sorted(CATEGORIES)}")
    setup = raw.get("setup", {})
    expect = raw.get("expect", {})
    after = raw.get("after", {})
    if not set(expect) <= EXPECT_KEYS:
        raise ValueError(f"{sid}: unknown expect keys {sorted(set(expect) - EXPECT_KEYS)}")
    if not set(after) <= AFTER_KEYS:
        raise ValueError(f"{sid}: unknown after keys {sorted(set(after) - AFTER_KEYS)}")
    turns_raw = raw.get("turns")
    if not turns_raw:
        raise ValueError(f"{sid}: needs at least one turn")
    turns = tuple(
        Turn(t["user"], _script(t.get("script", []), f"{sid}-t{i}"))
        for i, t in enumerate(turns_raw)
    )
    kb_raw = setup.get("kb", "real")
    kb = (
        None
        if kb_raw == "real"
        else tuple(Passage(p["source"], p["heading"], p["text"]) for p in kb_raw)
    )
    profile = setup.get("profile", ["balanced", 3, 5])
    mdv = setup.get("max_draft_value")
    return Scenario(
        id=sid,
        category=raw["category"],
        description=raw.get("description", ""),
        cash=Decimal(setup.get("cash", "1000")),
        holdings={k: int(v) for k, v in setup.get("holdings", {}).items()},
        profile=(str(profile[0]), int(profile[1]), int(profile[2])),
        kb=kb,
        max_draft_value=Decimal(mdv) if mdv is not None else None,
        turns=turns,
        expect=dict(expect),
        after=dict(after),
    )


def load_scenarios(directory: Path) -> list[Scenario]:
    """Load every `*.json` file (each holds a list of scenarios), sorted by scenario id."""
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.json")):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            scenarios.append(parse_scenario(raw))
    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario ids")
    return sorted(scenarios, key=lambda s: s.id)
