# ADR 0009: Evals check resulting state and measure noise

Status: accepted

## Context

A model reply can sound right while the system did something wrong, and a model run can pass or
fail by chance. Quality claims need checks on what actually happened, and a way to tell a real
change from random variation.

## Decision

### Scenarios

- The golden set is a list of JSON scenarios in `evals/scenarios`. Each one defines the account
  setup, one or more user turns, an optional later user action (confirm the last draft and run the
  worker), and the expected final state.
- Each scenario also holds a script for a fake model. The fake mode makes the whole suite
  deterministic and free, so it runs in CI as a gate. The live mode ignores the scripts and
  runs a real model.

### What is checked

- State: confirmed orders, executed orders, drafts and their statuses, tools called and not
  called, intent, and outcome class (ok, refused, fallback).
- Invariants for every scenario: the confirmation token never reaches the model context, every
  reply carries the disclaimer or is the fixed refusal, every number in the reply is backed by a
  tool output, and reconciliation finds no differences for the account.
- Reply wording is checked only for a few key phrases, never for exact text.

### Noise floor

Live runs are repeated. The standard deviation of the suite pass rate across repeats is the noise
floor. A change is reported as an improvement only when the pass rate delta is larger than the
noise floor of both reports. Scenarios that pass in some repeats and fail in others are listed as
flaky.

### Traces

Every agent run is stored in `agent_runs` and `agent_steps` with intent, outcome, latency, token
counts, and one row per model turn and tool call. Failures are categorised by reading these rows
before any dashboard is built.

## Consequences

- A prompt or router change can be measured instead of argued about.
- The fake mode proves the harness and the safety invariants. It does not prove model quality,
  which only the live mode can show.
- Live runs use the free tier quota, so they run on demand and not in CI.
