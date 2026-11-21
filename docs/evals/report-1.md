# Eval report 1: first runs and failure analysis

Date: 2026-11-21. Model: gemini-3.1-flash-lite through the Gemini API free tier.
Suite: 37 scenarios (see `evals/scenarios`), checked on resulting state (ADR 0009).
These are small samples on a small model, so treat the numbers as a baseline, not a claim.

## Results

| Run | Setup | Scenario runs | Passed | Pass rate |
|---|---|---|---|---|
| Fake mode, 3 repeats | scripted model, CI gate | 111 | 111 | 1.000 |
| Live 1 | no retries | 37 | 29 | 0.784 |
| Live 2 | retries on 429 and 5xx, 2 repeats | 74 | 73 | 0.986 |

Live 2 noise floor (standard deviation of the pass rate across repeats): 0.019. One scenario
(`portfolio-003`) was flaky. Token use was 125111 over the 74 live scenario runs, about 1700 per
scenario. Latency over the live runs was p50 3.6 s and p95 44.6 s, and the p95 is dominated by
retry waits.

## Failure taxonomy

Failures were read from the stored traces before anything else was built.

| Category | Live 1 | Live 2 | Upstream fix |
|---|---|---|---|
| Provider overloaded (HTTP 503) | 5 | 0 | Retry with growing delays (done in this phase) |
| Provider quota (HTTP 429) | 3 | 1 | Second provider as fallback, rate limiting, pacing (phase 5) |
| Wrong tool or wrong arguments | 0 | 0 | none needed yet |
| Numbers not backed by a tool output | 0 | 0 | none needed yet |
| Confirmation token seen by the model | 0 | 0 | none needed yet |
| Order confirmed or executed without the user | 0 | 0 | none needed yet |
| Reconciliation differences | 0 | 0 | none needed yet |

Every live failure was infrastructure, not model behaviour. The most upstream problem is provider
availability, so phase 5 starts there.

## What the evals caught before the live runs

- The router sent concept questions that mention the portfolio ("What is portfolio drift?") to the
  portfolio intent. Fixed by treating a concept question without personal wording as education.
- Two scenarios written by the delegated model counted orders inside a draft instead of order
  sets. The expectations were corrected.

## Limits of this report

- 37 scenarios written by the author (some drafted by a delegated coding model and reviewed), not by independent users.
- One small model, free tier, a single day.
- No LLM judge of explanation quality yet. It needs 50 human labels and an agreement score
  (Cohen's kappa), which is the next step of this phase.
