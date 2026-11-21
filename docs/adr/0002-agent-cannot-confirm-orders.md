# ADR 0002: The agent cannot confirm orders

Status: accepted

## Context

The agent is driven by an LLM. LLMs can hallucinate and can be manipulated by prompt injection,
including through retrieved content. If the agent could confirm an order, a single bad output
could trigger a trade, even in a simulation.

## Decision

- The tools exposed to the model are read only or draft only: `get_portfolio`,
  `get_target_allocation`, `compute_gap`, `prepare_orders`, `cancel_draft`, `search_knowledge`.
- There is no `confirm_order` tool.
- Confirmation is a separate HTTP endpoint called by a user action. It needs a random
  `confirmation_token` that is returned to the user interface, never to the model.
- A draft stores only the hash of the token, the hash of the order set, and an expiry time.
  The endpoint checks the token, the expiry, and that the order set has not changed.

## Consequences

- Safety is a property of the architecture, not of the prompt. Even a fully compromised model
  cannot execute an order.
- The user experience needs an explicit confirm step, which is the intended behaviour.
- Evals can assert the strong invariant: no confirmation, zero executed orders.
