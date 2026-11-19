# ADR 0004: PostgreSQL as source of truth, with outbox and idempotency

Status: accepted

## Context

Confirming an order must update state and trigger execution. Doing both in separate steps can
lose an order (state saved, event lost) or run it twice (retries, double clicks).

## Decision

- PostgreSQL is the single source of truth for accounts, holdings, orders, ledger, and audit.
- Confirming an order writes the order and an outbox event in one transaction.
- A separate worker reads outbox events, executes the simulated order, and writes ledger
  entries. Failures are retried with exponential backoff, then moved to a dead letter state.
- Every state changing request carries an idempotency key, so a repeated request returns the
  stored result instead of running again.
- A periodic reconciliation job compares the ledger with executed orders and reports differences.

## Consequences

- No lost or duplicated orders by construction, which failure tests will prove.
- More moving parts than a direct call, which is the cost of reliability.
- Every query must check that the account belongs to the caller, to prevent access to other
  users data.
