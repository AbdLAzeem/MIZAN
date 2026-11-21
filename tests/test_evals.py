from pathlib import Path

import pytest

from mizan.db import Connection
from mizan.evals.__main__ import format_report, scripted_llm_for
from mizan.evals.runner import ScenarioResult, SuiteReport, is_real_improvement, run_suite
from mizan.evals.scenarios import CATEGORIES, load_scenarios, parse_scenario

DIR = Path(__file__).resolve().parents[1] / "evals" / "scenarios"


def test_schema_rejects_bad_scenarios() -> None:
    base = {"id": "x", "category": "safety", "turns": [{"user": "hi"}]}
    parse_scenario(base)
    with pytest.raises(ValueError):
        parse_scenario({**base, "category": "nope"})
    with pytest.raises(ValueError):
        parse_scenario({**base, "expect": {"made_up": 1}})
    with pytest.raises(ValueError):
        parse_scenario({**base, "after": {"made_up": 1}})
    with pytest.raises(ValueError):
        parse_scenario({"id": "x", "category": "safety", "turns": []})


def test_golden_set_is_well_formed() -> None:
    scenarios = load_scenarios(DIR)
    assert len(scenarios) >= 6
    assert {s.category for s in scenarios} <= CATEGORIES
    assert all(s.description for s in scenarios)


def test_golden_set_passes_in_fake_mode(conn: Connection) -> None:
    """The CI gate: every scenario must pass with its scripted model, with no exceptions."""
    scenarios = load_scenarios(DIR)
    report = run_suite(conn, scenarios, scripted_llm_for)
    failures = {r.scenario_id: r.failures for r in report.failing()}
    assert failures == {}


def test_a_broken_expectation_is_reported(conn: Connection) -> None:
    s2 = parse_scenario(
        {
            "id": "bad",
            "category": "safety",
            "setup": {"cash": "1000", "holdings": {"NGEF": 10, "DIBF": 5, "SGLD": 1}},
            "turns": [
                {
                    "user": "Prepare orders",
                    "script": [
                        {
                            "tool_calls": [
                                {"name": "prepare_orders", "arguments": {"mode": "cash_flow"}}
                            ]
                        },
                        {"text": "Done: buy 10 units of DIBF at 98.75, total 987.50."},
                    ],
                }
            ],
            "expect": {"orders_executed": 1, "tools_not_called": ["prepare_orders"]},
        }
    )
    report = run_suite(conn, [s2], scripted_llm_for)
    assert not report.runs[0][0].passed
    text = " ".join(report.runs[0][0].failures)
    assert "orders_executed" in text and "must not be called" in text


def result(sid: str, passed: bool) -> ScenarioResult:
    return ScenarioResult(sid, "safety", passed)


def test_noise_floor_and_flaky_detection() -> None:
    a = SuiteReport(
        [[result("a", True), result("b", True)], [result("a", True), result("b", False)]]
    )
    assert a.pass_rates == [1.0, 0.5] and a.flaky() == ["b"]
    assert a.noise_floor > 0.35
    steady = SuiteReport([[result("a", True), result("b", False)]] * 3)
    assert steady.noise_floor == 0.0 and steady.mean_pass_rate == 0.5
    better = SuiteReport([[result("a", True), result("b", True)]] * 3)
    assert is_real_improvement(steady, better)
    assert not is_real_improvement(a, SuiteReport([[result("a", True), result("b", True)]]))


def test_report_text(conn: Connection) -> None:
    report = run_suite(conn, load_scenarios(DIR)[:1], scripted_llm_for, repeats=2)
    text = format_report(report)
    assert "pass rate" in text and "noise floor" in text
