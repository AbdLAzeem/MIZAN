# ADR 0003: A deterministic engine owns all numbers

Status: accepted

## Context

LLMs are unreliable at arithmetic and at being consistent between runs. Allocation, gaps,
drift, order sizes, and balances must be exact and reproducible.

## Decision

- All financial calculations live in a plain Python engine with no LLM calls.
- Monetary values use `Decimal`, never `float`.
- Tool inputs and outputs are validated with Pydantic models.
- The LLM only turns engine output into prose. A check compares every number in a reply with
  the tool outputs of that run (numeric faithfulness).
- The engine is tested with unit tests and property based tests. For example, target weights
  always sum to 100 percent.

## Consequences

- Results are reproducible and testable without any model.
- The engine can be reasoned about and reviewed on its own.
- The prose layer can be swapped or fine tuned without touching the numbers.
