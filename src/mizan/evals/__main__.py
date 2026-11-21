"""Command line entry: python -m mizan.evals --mode fake|live [--repeats N] [--out FILE]."""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from mizan.agent.gemini import GeminiLLM
from mizan.agent.types import AgentLLM, ScriptedLLM
from mizan.db import Connection, connect, migrate
from mizan.evals.runner import SuiteReport, run_suite
from mizan.evals.scenarios import Scenario, load_scenarios

ROOT = Path(__file__).resolve().parents[3]
SCENARIO_DIR = ROOT / "evals" / "scenarios"
SEED = ROOT / "data" / "seed" / "instruments.json"


def seed_instruments(conn: Connection) -> None:
    for item in json.loads(SEED.read_text(encoding="utf-8")):
        conn.execute(
            "INSERT INTO instruments (symbol, name, asset_class, price) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (symbol) DO NOTHING",
            (item["symbol"], item["name"], item["asset_class"], item["price"]),
        )


def scripted_llm_for(scenario: Scenario) -> AgentLLM:
    turns = [t for turn in scenario.turns for t in turn.script]
    return ScriptedLLM(turns)


def format_report(report: SuiteReport) -> str:
    lines = [
        f"scenarios per run: {len(report.runs[0])}, repeats: {len(report.runs)}",
        f"pass rate: {report.mean_pass_rate:.3f}, noise floor (stdev): {report.noise_floor:.3f}",
    ]
    latencies = sorted(r.latency_ms for run in report.runs for r in run)
    if latencies:
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[max(int(len(latencies) * 0.95) - 1, 0)]
        lines.append(f"latency ms p50: {p50}, p95: {p95}")
    tokens = sum(r.input_tokens + r.output_tokens for run in report.runs for r in run)
    lines.append(f"total tokens: {tokens}")
    if report.flaky():
        lines.append(f"flaky scenarios: {', '.join(report.flaky())}")
    for r in report.failing():
        lines.append(f"FAIL {r.scenario_id}: {'; '.join(r.failures)}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizan.evals")
    parser.add_argument("--mode", choices=["fake", "live"], default="fake")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--category")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    url = os.environ.get("EVAL_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("Set EVAL_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 2
    scenarios = load_scenarios(SCENARIO_DIR)
    if args.category:
        scenarios = [s for s in scenarios if s.category == args.category]
    if args.limit:
        scenarios = scenarios[: args.limit]
    conn = connect(url)
    migrate(conn)
    seed_instruments(conn)
    if args.mode == "live":
        live = GeminiLLM.from_env()
        report = run_suite(conn, scenarios, lambda _s: live, args.repeats)
    else:
        report = run_suite(conn, scenarios, scripted_llm_for, args.repeats)
    print(format_report(report))
    if args.out:
        payload = [[dict(r.__dict__) for r in run] for run in report.runs]
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if not report.failing() else 1


if __name__ == "__main__":
    raise SystemExit(main())
