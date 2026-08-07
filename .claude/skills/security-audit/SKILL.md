---
name: security-audit
description: Security audit of PublicHost-Backend (defensive) — SSH session handling, subdomain validation, Host-header proxy routing, Redis/RabbitMQ usage, secrets in logs. Args = optional scope (path or feature); default is the whole src/.
---

# Security audit

Defensive audit of this public SSH tunneling service. Reply in the same
language as the user's request. Report findings only — do not fix unless asked.

## Checklist (go through all of it for the given scope)

1. **SSH layer (`asyncssh`)** — auth path (passwordless login boundaries),
   per-connection resource limits, tunnel lifetime enforcement, what happens
   on abrupt disconnect.
2. **Subdomain labels** — validation of `<label>.<base_domain>`: charset,
   length, reserved names (www, api, admin), collision handling.
3. **HTTP reverse proxy** — Host-header routing: header smuggling, spoofed
   `X-Forwarded-*`, request size limits, WebSocket upgrade handling.
4. **Redis / RabbitMQ / Celery** — key namespacing, TTLs on ephemeral keys,
   what leaks if Redis is compromised, task argument injection.
5. **FastAPI + PostgreSQL** — authz on every endpoint, ORM/raw-SQL usage,
   error responses leaking internals.
6. **Logs (WebLogs, CSV)** — credentials/tokens/PII in logs, path traversal
   in CSV file naming, who can read whose logs via WebSocket.
7. **Docker / deploy** — secrets in images or compose files, exposed ports,
   running as root.

## Output format

For each finding: severity (Critical/High/Medium/Low), file:line, one-sentence
defect, concrete attack scenario, one-line recommended fix. Sort by severity.
End with a short verdict paragraph. If a checklist area is clean, one line:
"N. <area> — clean."
