# 0001 — Record architecture decisions

## Status

Proposed

## Context

Battery-Emulator is safety-relevant firmware maintained by a distributed
community. Many of its important behaviors are deliberate policies rather
than incidental code: which fault conditions open contactors versus only
zeroing power limits, how long battery CAN silence is tolerated before the
system faults, how tasks on the two ESP32 cores share the `datalayer`, and
how battery/inverter drivers are registered. The rationale for these choices
currently lives in code comments, PR discussions, and maintainers' memory,
which makes them hard to discover and easy to accidentally reverse.

## Decision

We will record architecturally significant decisions as Architecture Decision
Records in `docs/adr/`, using the conventions described in
[README.md](README.md) and the format in [template.md](template.md), as
described by Michael Nygard and collected at
<https://github.com/architecture-decision-record/architecture-decision-record>.

## Consequences

- New contributors can discover why the system behaves the way it does
  without archaeology through PR threads.
- Changes to recorded policies must engage with the documented reasoning,
  which raises the quality of safety-relevant discussions.
- Writing an ADR adds a small amount of overhead to significant changes;
  routine bug fixes and driver additions do not need one.
