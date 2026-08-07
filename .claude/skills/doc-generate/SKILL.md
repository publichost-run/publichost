---
name: doc-generate
description: Generate/update the docs/ directory for PublicHost-Backend — architecture overview, component guides, and the frontend integration guide (how the Frontend team uses the API and WebLogs WebSocket). Args = optional scope; default is full docs/.
---

# Documentation generator

Reply in the user's language. The documents themselves are always in English
(project convention). Everything in the docs must be verified against the
actual code — never write from memory; every endpoint, env var, message field,
and command must exist in `src/` or config files.

## Target structure

```
docs/
  README.md              # index: what each doc is for, who reads it
  architecture.md        # components + data flow
  components/
    ssh-server.md        # server.py: auth, banner session, ssh -R, extend
    proxy.md             # proxy.py: Host routing, tunnel ports, WebLogs capture
    registry.md          # registry.py: subdomain lifecycle, lifetimes
    api.md               # FastAPI app, models, Celery tasks, Redis consumer
  frontend-guide.md      # for the Frontend team (see below)
  operations.md          # run locally, Docker, env vars, nginx, key rotation
```

## Steps

1. Read `AGENTS.md`, then the source for each area being documented.
2. `architecture.md`: one Mermaid diagram of the data flow
   (SSH client → server.py → registry → proxy → nginx; events → Redis →
   API/Celery → PostgreSQL; WebLogs → WebSocket → Frontend), then a short
   section per component: responsibility, key files, what talks to what.
3. `frontend-guide.md` — the most important one, written for frontend
   developers who don't read Python:
   - base URLs and environments (from `PUBLICHOST_FRONTEND_DOMAIN` et al.)
   - every FastAPI endpoint: method, path, auth, request/response JSON
     examples, error shapes
   - WebLogs WebSocket: connect URL `/channel/weblogs?token=`, where the
     token comes from, every message type with a real JSON example,
     reconnect/close behavior
   - the user journey: what the SSH banner shows the user, which links
     point at the frontend, what the frontend is expected to render
4. `operations.md`: local run, Docker Compose, all `PUBLICHOST_*` env vars in
   a table (name, default, used by), nginx example, host-key rotation.
5. If `docs/` already exists — update in place, don't regenerate blindly;
   list breaking API changes at the top of `frontend-guide.md`.
6. Finish by listing which files were written/updated and anything found in
   code that contradicts existing docs.
