# Architecture Decision Records

This directory holds [Architecture Decision Records
(ADRs)](https://github.com/architecture-decision-record/architecture-decision-record):
short documents that capture one architecturally significant decision each —
the context that forced the decision, the decision itself, and its
consequences.

Why: Battery-Emulator encodes many deliberate policies (event severity vs.
contactor behavior, CAN liveness timeouts, the `datalayer` concurrency model,
driver registration) whose rationale currently lives only in code comments and
PR threads. ADRs make those choices durable and revisitable, so future changes
argue against the recorded reasoning instead of rediscovering it.

## Conventions

- One decision per file, numbered sequentially:
  `NNNN-short-kebab-case-title.md`.
- Use [template.md](template.md) (Michael Nygard's format: Status / Context /
  Decision / Consequences).
- ADRs are immutable once accepted. If a decision changes, write a new ADR
  that supersedes the old one and update the old ADR's Status line to
  `Superseded by [NNNN](NNNN-....md)`.
- Propose an ADR as a pull request so the discussion happens in review.

## Index

- [0001 — Record architecture decisions](0001-record-architecture-decisions.md)
