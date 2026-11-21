-- Traces of agent runs, used for failure analysis and evals (phase 4).

CREATE TABLE agent_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES accounts (id),
    started_at timestamptz NOT NULL DEFAULT now(),
    user_message text NOT NULL,
    intent text,
    reply text,
    steps integer NOT NULL DEFAULT 0,
    corrected boolean NOT NULL DEFAULT false,
    outcome text NOT NULL CHECK (outcome IN ('ok', 'fallback', 'refused', 'error')),
    latency_ms integer NOT NULL DEFAULT 0,
    input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    error text
);
CREATE INDEX agent_runs_started_idx ON agent_runs (started_at);

CREATE TABLE agent_steps (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    idx integer NOT NULL,
    kind text NOT NULL CHECK (kind IN ('model', 'tool')),
    name text NOT NULL,
    ok boolean NOT NULL,
    latency_ms integer NOT NULL DEFAULT 0,
    detail jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX agent_steps_run_idx ON agent_steps (run_id, idx);
