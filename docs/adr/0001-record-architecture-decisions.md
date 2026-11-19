# ADR 0001: Record architecture decisions

Status: accepted

## Context

The project makes several decisions that are easy to forget and hard to justify later, such as
why the agent has no confirm tool. A short written record keeps the reasons next to the code.

## Decision

We keep one short markdown file per significant decision in `docs/adr`, numbered in order,
with the sections Status, Context, Decision, and Consequences.

## Consequences

- New contributors can read why things are the way they are.
- Changing a decision means writing a new ADR that supersedes the old one.
- Writing takes a few minutes per decision, which is acceptable.
