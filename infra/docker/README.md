# infra/docker

The Docker Compose file for local development (PostgreSQL 16, bound to 127.0.0.1:5442) lives at the repository root as `docker-compose.yml`, because it is part of the everyday dev workflow (`make db-up`). This folder is reserved for future container builds — for example a control-plane image if hybrid deployment ([../../docs/adr/ADR-001-local-first-hybrid-ready.md](../../docs/adr/ADR-001-local-first-hybrid-ready.md)) ever calls for one. Nothing here is used today.
