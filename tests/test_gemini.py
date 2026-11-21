import os
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from mizan.agent.gemini import GeminiLLM, to_contents, to_tools
from mizan.agent.tools import tool_specs
from mizan.agent.types import ChatMessage, ToolCall


def test_conversion_groups_tool_results_and_names_them() -> None:
    call_a, call_b = ToolCall("a", "get_portfolio", {}), ToolCall("b", "compute_gap", {})
    system, contents = to_contents(
        [
            ChatMessage("system", "rules"),
            ChatMessage("user", "hi"),
            ChatMessage("assistant", "", tool_calls=(call_a, call_b)),
            ChatMessage("tool", '{"ok": true}', tool_call_id="a"),
            ChatMessage("tool", '{"ok": false, "error": "x"}', tool_call_id="b"),
            ChatMessage("assistant", "done"),
        ]
    )
    assert system == "rules"
    assert [c.role for c in contents] == ["user", "model", "user", "model"]
    calls = contents[1].parts or []
    assert [p.function_call.name for p in calls if p.function_call] == [
        "get_portfolio",
        "compute_gap",
    ]
    responses = contents[2].parts or []
    assert [p.function_response.name for p in responses if p.function_response] == [
        "get_portfolio",
        "compute_gap",
    ]


def test_tools_use_the_json_schema_and_no_confirm_tool_exists() -> None:
    declared = to_tools(tool_specs())[0].function_declarations or []
    assert len(declared) == 6
    assert not any("confirm" in (d.name or "") for d in declared)


class FakeModels:
    def __init__(self, parts: list[Any]) -> None:
        self.parts = parts
        self.kwargs: dict[str, Any] = {}

    def generate_content(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        content = SimpleNamespace(parts=self.parts)
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def part(**kw: Any) -> Any:
    base = {"text": None, "thought": None, "function_call": None}
    return SimpleNamespace(**(base | kw))


def test_turn_parses_text_and_function_calls() -> None:
    fake = SimpleNamespace(
        models=FakeModels(
            [
                part(text="Hello"),
                part(function_call=SimpleNamespace(id=None, name="get_portfolio", args={})),
            ]
        )
    )
    llm = GeminiLLM(fake, model="m")
    out = llm.turn([ChatMessage("system", "s"), ChatMessage("user", "u")], tool_specs())
    assert out.text == "Hello"
    assert out.tool_calls == (ToolCall("call_1", "get_portfolio", {}),)
    assert fake.models.kwargs["model"] == "m"
    cfg: types.GenerateContentConfig = fake.models.kwargs["config"]
    assert cfg.system_instruction == "s"
    assert cfg.automatic_function_calling is not None and cfg.automatic_function_calling.disable


class FlakyModels:
    def __init__(self, failures: list[int]) -> None:
        self.failures = failures
        self.calls = 0

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.failures:
            err = Exception("provider error")
            err.code = self.failures.pop(0)  # type: ignore[attr-defined]
            raise err
        content = SimpleNamespace(parts=[part(text="ok")])
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def test_transient_errors_are_retried_with_growing_delays() -> None:
    models = FlakyModels([503, 429])
    delays: list[float] = []
    llm = GeminiLLM(SimpleNamespace(models=models), sleep=delays.append)
    assert llm.turn([ChatMessage("user", "hi")], []).text == "ok"
    assert models.calls == 3 and delays == [2.0, 6.0]


def test_permanent_errors_and_exhausted_retries_are_raised() -> None:
    bad = FlakyModels([400])
    with pytest.raises(Exception, match="provider error"):
        GeminiLLM(SimpleNamespace(models=bad), sleep=lambda _s: None).turn([], [])
    assert bad.calls == 1
    down = FlakyModels([503, 503, 503, 503, 503])
    with pytest.raises(Exception, match="provider error"):
        GeminiLLM(SimpleNamespace(models=down), sleep=lambda _s: None).turn([], [])
    assert down.calls == 4


def test_from_env_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        GeminiLLM.from_env()


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call the real API")
def test_live_gemini_answers_a_simple_question() -> None:
    llm = GeminiLLM.from_env()
    out = llm.turn([ChatMessage("user", "Reply with the single word OK.")], [])
    assert out.text and "ok" in out.text.lower()


@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call the real API")
def test_live_agent_flow_keeps_every_invariant(conn: Any, make_account: Any) -> None:
    from pathlib import Path

    from mizan.agent.agent import run_agent
    from mizan.agent.knowledge import load_passages
    from mizan.agent.tools import ToolContext
    from mizan.disclaimer import DISCLAIMER

    a = make_account("1000", {"NGEF": 10, "DIBF": 5, "SGLD": 1})
    kb = load_passages(Path(__file__).resolve().parents[1] / "data" / "knowledge")
    ctx = ToolContext(conn, a.user_id, a.account_id, kb=kb)
    llm = GeminiLLM.from_env()
    r = run_agent(llm, ctx, "Prepare orders to rebalance my portfolio to the target allocation.")
    print("REPLY:", r.reply, "| tools:", r.tool_calls, "| corrected:", r.corrected)
    assert r.reply.endswith(DISCLAIMER)
    orders = conn.execute("SELECT count(*) AS n FROM orders").fetchone()
    assert orders["n"] == 0  # the model can never execute anything
    for ui in r.ui:
        assert all(ui["token"] not in m.content for m in r.messages)
