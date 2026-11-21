# ADR 0005: Development environment and testing without real LLMs

Status: accepted

## Context

The development machine is modest: an older dual core CPU, 8 GB of RAM, no discrete GPU, and
no Docker. Real LLM calls are slow, cost money or quota, and are not deterministic.

## Decision

- Tests and CI never call a real LLM provider. They use `FakeLLM`, a scripted implementation
  of the `LLM` protocol that returns replies in order and records calls.
- Local models are not used for development. Model training, if done, runs on free cloud GPUs.
- CI runs PostgreSQL with pgvector as a GitHub Actions service container, so database tests run
  there without local Docker. Locally they are skipped unless `DATABASE_URL` is set.
- Tooling is plain Python 3.12 with a virtual environment, ruff, mypy in strict mode, pytest,
  and hypothesis.

## Consequences

- The test suite is fast, free, and deterministic.
- Real model behaviour is covered by separate evals that run on demand, not on every commit.
- Secrets stay in a local `.env` file that is never committed.
