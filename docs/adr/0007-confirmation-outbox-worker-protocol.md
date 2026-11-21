# ADR 0007: Confirmation, outbox, and worker protocol

Status: accepted

## Context

ADR 0002 says only a user action can confirm an order, and ADR 0004 says confirmation and
execution are linked by an outbox. This ADR fixes the exact protocol so it can be tested.

## Decision

### Drafts

- A draft stores the planned orders, the mode, the SHA-256 hash of the canonical order set,
  the SHA-256 hash of a random confirmation token, and an expiry 10 minutes after creation.
- The plain token is returned once, to the caller that created the draft, and is never stored.

### Confirm (one database transaction)

1. Look up the idempotency key for this account. If it exists with the same request hash,
   return the stored response. If the hash differs, reject with a conflict error.
2. Lock the draft row (`SELECT ... FOR UPDATE`) and check that it belongs to the caller's
   account, is `open`, is not expired, and that the token hash matches (constant time compare).
3. Recompute the order set hash from the stored orders and compare it with the stored hash.
4. Insert the `orders` row, mark the draft `confirmed`, insert one `outbox_events` row of type
   `order.confirmed`, write an `audit_log` row, and store the idempotency response.

Any failure rolls the whole transaction back, so there is never an order without an event.

### Worker

- Claim pending events whose `next_attempt_at` has passed with
  `FOR UPDATE SKIP LOCKED`, so several workers never take the same event.
- Execution runs in one transaction per event: lock the order, skip it if it is already
  `executed` (this makes duplicate events harmless), check cash and units, update balance and
  holdings at the instrument price, write balanced double-entry ledger lines, mark the order
  `executed`, and mark the event `done`.
- On error the transaction rolls back, then the event gets `attempts + 1`, `last_error`, and
  `next_attempt_at = now + 2^attempts seconds`. After 5 attempts the event becomes `dead`
  and the order becomes `failed`.
- If the worker dies mid event, nothing was committed, so the event stays claimable.

### Ledger

Each executed order set is one `transaction_id`. A buy debits `position:<account>:<symbol>`
and credits `cash:<account>`. A sell does the reverse. Debits equal credits per transaction.

### Reconciliation

A job compares, per account, the cash balance and holdings with a replay of the executed
orders and the ledger, and checks that debits equal credits per transaction. It returns a list
of differences and never fixes anything by itself.

### Authorization

Every service function takes the caller's `user_id` and checks that the account belongs to it.
Unknown and foreign accounts give the same error, so account ids cannot be probed.

## Consequences

- Duplicate events, double clicks, worker crashes, and replayed requests are all safe.
- The protocol is testable with a real PostgreSQL only, because locking is part of the design.
- Events that fail repeatedly stay visible as `dead` for manual review.
