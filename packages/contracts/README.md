# @nexus/contracts

Shared API contract types between the control plane and the web dashboard.

Status: scaffolded. The web dashboard currently defines its API types locally in
`apps/web/src/lib/api.ts`, mirrored from the control plane's Pydantic schemas in
`services/control-plane/src/nexus/api/schemas.py`. When the contract surface
stabilizes, generate TypeScript types from the control plane's OpenAPI document
(`GET /openapi.json`) into this package and consume them from the dashboard.
