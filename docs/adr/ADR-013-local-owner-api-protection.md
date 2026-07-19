# ADR-013: Local-owner API protection beyond localhost binding

- Status: accepted
- Date: 2026-07-15

## Context

The control plane binds to 127.0.0.1, which stops remote hosts — but localhost binding alone does **not** stop the two realistic attacks against a local control plane that can execute code. First, DNS rebinding: a malicious web page resolves its own domain to 127.0.0.1 and the owner's browser happily sends requests to the API from that page's origin. Second, cross-site request forgery: a hostile page can fire-and-forget form POSTs at `http://localhost:8400` without needing to read the response. Both would let an attacker create goals, approve gates, or change repository trust with the owner's browser as the confused deputy. Full user authentication is planned (single owner today, Entra ID considered), but these browser-origin attacks needed closing now.

## Decision

A protection middleware in the FastAPI app (`src/nexus/api/app.py`) enforces, on every request:

- **Host-header allowlist**: only `localhost` and `127.0.0.1` are accepted; anything else gets HTTP 421. This defeats DNS rebinding, because a rebound request arrives carrying the attacker's domain in the Host header.
- **Custom header on state changes**: every POST/PUT/PATCH/DELETE must carry the `X-Nexus-Client` header or it gets HTTP 403. A custom header makes cross-origin requests non-simple, forcing a CORS preflight that the browser will fail against the restricted CORS policy — so a hostile page cannot fire-and-forget form POSTs at the control plane.
- **CORS restricted** to the local dashboard origins (`http://localhost:3400`, `http://127.0.0.1:3400`).
- Security response headers on everything: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.

The dashboard and CLI send the header; integration tests pin the 421/403 behavior.

There is still **no user authentication**: any non-browser local process can call the API directly. That is a documented residual risk (single owner, local machine), and authentication (potentially Entra ID) is planned before any remote or multi-user step.

## Consequences

- The two cheap browser-borne attacks — DNS rebinding and CSRF form posts — are closed without introducing credentials, sessions, or user management.
- The protection is honest about its limits: it authenticates the *request shape*, not the caller. Local malware with the ability to make HTTP requests is out of scope until real authentication lands ([../THREAT-MODEL.md](../THREAT-MODEL.md)).
- Any new client (scripts, tooling) must send `X-Nexus-Client` on state-changing calls; this is a one-line cost and a deliberate speed bump against accidental integrations.
- Binding to anything other than localhost remains an always-gated action; this ADR does not change the network boundary, it hardens what already sits inside it.

## Update (2026-07-20 acceptance pass)

The custom-header check alone was correctly flagged as not being
authentication. The implementation now verifies a real credential: a
cryptographically random owner token generated on first use, stored in
`~/.nexus/owner-token` (chmod 600, git-ignored), compared in constant time on
every state-changing request. The dashboard never sees the token: its
same-origin Next.js proxy (`/api/nexus/...`) reads the file server-side and
attaches the header on the loopback hop. An Origin allowlist additionally
rejects cross-origin state changes. Host allowlist, strict CORS, and the
preflight-forcing client header remain as further layers.
