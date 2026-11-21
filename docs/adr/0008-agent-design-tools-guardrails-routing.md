# ADR 0008: Agent design: tools, guardrails, and routing

Status: accepted

## Context

ADR 0002 says the agent cannot confirm orders and ADR 0003 says code owns every number.
The agent layer must make both properties hold even when the model is wrong or manipulated.

## Decision

### Tools

- Six tools only: `get_portfolio`, `get_target_allocation`, `compute_gap`, `prepare_orders`,
  `cancel_draft`, `search_knowledge`. A call to any other name returns an `unknown_tool`
  error to the model and has no effect.
- Arguments are validated with Pydantic. A validation failure is returned to the model as a
  tool error, not raised.
- Every tool runs with a context of connection, user id, and account id, so the ownership
  checks of the service layer apply to the agent as well.
- `prepare_orders` returns the draft summary to the model without the confirmation token. The
  token travels out of band in the run result for the user interface only. The model never
  sees it, so it cannot leak it or use it.
- A draft is limited to 10 orders and a total value of 50000 per draft.

### Numeric faithfulness

Every number in the final reply must appear in a tool output of the same run, compared by
numeric value, or be one of a small allowed set (0, 1, 2, 3, 10, 100). On failure the agent
adds a corrective message and retries once. If the retry fails too, the user gets a fixed
reply that shows no unverified numbers.

### Retrieved text is data

`search_knowledge` returns passages inside a field marked as untrusted reference text. A
passage that looks like an instruction to the model (for example it tells the model to ignore
rules or to confirm something) is dropped before the model sees it.

### Routing

Before any model call, a rule based router classifies the message as education, portfolio,
orders, or out of scope. Out of scope messages, including requests for personal advice,
get a fixed refusal and the model is never called. This baseline is later compared with a
prompt router and a small fine tuned router (phase 6).

### Run loop

A LangGraph graph with the nodes route, model, tools, and guard, limited to 6 model steps.
Every answer from the model gets the fixed educational disclaimer appended by code.

## Consequences

- The strongest safety properties are structural: no confirm tool, no token in model context,
  ownership checks in services.
- The numeric check is strict. A model that rounds a number differently is corrected or
  replaced by the fixed reply, which costs some fluency and is accepted.
- The router is crude. Its mistakes are measured in the evals, not hidden.
