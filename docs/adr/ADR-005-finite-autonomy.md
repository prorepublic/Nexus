# ADR-005: Explicit state machines and bounded loops, not an agent framework

- Status: accepted
- Date: 2026-07-14

## Context

Agent frameworks (LangGraph, CrewAI, AutoGen, and similar) promise orchestration out of the box: graphs, retries, memory, multi-agent handoffs. But Nexus's core requirements are auditability, predictability, and hard limits on autonomous behavior — a system that operates on the owner's real repositories with the owner's credentials must never be in a state nobody can explain. Frameworks add a large dependency surface, their own abstractions over control flow, and failure modes that are hard to bound; the orchestration Nexus actually needs (a queue, a dispatch loop, validation, a repair budget) is small and well understood.

## Decision

Nexus uses hand-written, explicit state machines (`src/nexus/domain/transitions.py`) for Goals, Tasks, and Runs. Every legal transition is enumerated; anything else raises `InvalidTransition` rather than corrupting state. The orchestration loop is plain, synchronous Python.

Every run is bounded: repair attempts (max_repair_attempts=2, so at most 3 attempts per task), task timeouts (1800 s), command timeouts (600 s), output caps (512 KB), bounded worker turns, cancellation support at run and goal level, and a full audit trail in `audit_events`.

Agent frameworks may be evaluated later, but only against a demonstrated, concrete added value that the explicit approach cannot provide — and adopting one would require a superseding ADR.

## Consequences

- Every state the system can be in, and every way it can move between states, is readable in one file and enforced at runtime; tests pin the transition tables.
- No framework dependency churn, no hidden retries, no unbounded loops; "why did this happen" is always answerable from the audit trail.
- The cost is writing orchestration primitives ourselves (queue, dispatch, repair) — acceptable because they total a few hundred lines and are individually tested.
- New workflow shapes (parallel fan-out, richer review loops) must be designed deliberately instead of inherited from a framework. That friction is considered a feature: autonomy grows only when explicitly granted and bounded ([../OPERATING-MODEL.md](../OPERATING-MODEL.md)).
