-- Mizan schema, version 1. Applied in order by mizan.db.migrate.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL
);

CREATE TABLE accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users (id),
    currency text NOT NULL DEFAULT 'USD',
    cash_balance numeric(18, 2) NOT NULL CHECK (cash_balance >= 0),
    opening_cash numeric(18, 2) NOT NULL CHECK (opening_cash >= 0)
);

CREATE TABLE instruments (
    symbol text PRIMARY KEY,
    name text NOT NULL,
    asset_class text NOT NULL CHECK (asset_class IN ('equity', 'fixed_income', 'gold')),
    price numeric(18, 4) NOT NULL CHECK (price > 0)
);

CREATE TABLE holdings (
    account_id uuid NOT NULL REFERENCES accounts (id),
    symbol text NOT NULL REFERENCES instruments (symbol),
    units integer NOT NULL CHECK (units >= 0),
    opening_units integer NOT NULL DEFAULT 0 CHECK (opening_units >= 0),
    PRIMARY KEY (account_id, symbol)
);

CREATE TABLE target_profiles (
    account_id uuid PRIMARY KEY REFERENCES accounts (id),
    goal text NOT NULL CHECK (goal IN ('preserve', 'balanced', 'growth')),
    risk integer NOT NULL CHECK (risk BETWEEN 1 AND 5),
    horizon_years integer NOT NULL CHECK (horizon_years >= 0),
    weights jsonb NOT NULL
);

CREATE TABLE order_drafts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES accounts (id),
    mode text NOT NULL CHECK (mode IN ('cash_flow', 'rebalance')),
    orders jsonb NOT NULL,
    order_set_hash text NOT NULL,
    confirmation_token_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'confirmed', 'cancelled', 'expired')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    draft_id uuid NOT NULL UNIQUE REFERENCES order_drafts (id),
    account_id uuid NOT NULL REFERENCES accounts (id),
    status text NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('confirmed', 'executed', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    executed_at timestamptz
);

CREATE TABLE ledger_entries (
    id bigserial PRIMARY KEY,
    transaction_id uuid NOT NULL,
    order_id uuid NOT NULL REFERENCES orders (id),
    ledger_account text NOT NULL,
    debit numeric(18, 2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit numeric(18, 2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((debit = 0) <> (credit = 0))
);
CREATE INDEX ledger_entries_order_idx ON ledger_entries (order_id);

CREATE TABLE audit_log (
    id bigserial PRIMARY KEY,
    actor text NOT NULL CHECK (actor IN ('user', 'agent', 'system')),
    action text NOT NULL,
    account_id uuid,
    payload jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox_events (
    id bigserial PRIMARY KEY,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'done', 'dead')),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX outbox_pending_idx ON outbox_events (next_attempt_at) WHERE status = 'pending';

CREATE TABLE idempotency_keys (
    key text NOT NULL,
    account_id uuid NOT NULL REFERENCES accounts (id),
    request_hash text NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (key, account_id)
);
