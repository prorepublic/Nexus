# ADR-001: Local-first control plane, hybrid-ready by design

- Status: accepted
- Date: 2026-07-14

## Context

Nexus orchestrates AI workers that read and write the owner's source code and operate with the owner's credentials (gh CLI auth, Notion token, subscription-authenticated worker CLIs). Hosting the control plane remotely would mean shipping source code, credentials, or both to infrastructure someone else operates, and would immediately create hosting costs, an authentication problem, and a much larger attack surface. At the same time, the owner will eventually want to monitor and steer Nexus away from the desk, so a pure "never leaves this machine" stance would be a dead end.

## Decision

The control plane runs locally. The FastAPI server binds to 127.0.0.1:8400, PostgreSQL to 127.0.0.1:5442, the dashboard to localhost:3400, and CORS is restricted to the local dashboard origin. Nothing listens on a public interface; `expose-service-publicly` is an always-gated approval action.

Hybrid deployment comes later under one non-negotiable principle: **sensitive code and credentials stay local.** Remote surfaces receive status and metadata only, delivered through explicitly authorized runners. Secrets are never synchronized off the machine.

## Consequences

- Zero hosting cost and no remote attack surface today; the network boundary is the loopback interface.
- No authentication was required for the bootstrap; the localhost API runs unauthenticated as a single-owner placeholder. This is an accepted, documented residual risk ([../THREAT-MODEL.md](../THREAT-MODEL.md)) and auth is planned before any hybrid step.
- Availability is bounded by the owner's machine: no laptop, no orchestration. Accepted for this phase.
- Future hybrid work is additive (status export, authorized runners) rather than a re-architecture, because the boundary was drawn around secrets from the start.
