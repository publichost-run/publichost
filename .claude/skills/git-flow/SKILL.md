---
name: git-flow
description: Git workflow for PublicHost-Backend — branch, commit, and PR conventions. Use when finishing a task (commit/PR), starting work on a feature (branch), or when the user asks to "закоммить" / "сделай PR". Args = optional short task description.
---

# Git flow for PublicHost-Backend

Reply in the user's language. Branch names, commit messages, and PR text are
always in English. Git mutations only on explicit user request (per AGENTS.md).

## Branching

- Base branch: `main`. Never commit directly to `main` — create a branch first.
- Naming: `<type>/<short-kebab-scope>`, types match the project roadmap:
  - `feat/` — new functionality (e.g. `feat/abuse-rate-limit`,
    `feat/multi-tunnel`)
  - `fix/` — bug fix (e.g. `fix/proxy-host-header-case`)
  - `refactor/`, `docs/`, `test/`, `chore/` — as named.
- One branch = one task. Do not mix a feature with unrelated cleanup.

## Before every commit (hard gate)

Run and require green, in this order:

```bash
uv run ruff format .
uv run ruff check .
uv run pytest -q
```

If pytest is red — do not commit; report the failure instead.
Never commit: `.env`, `.env.docker`, host keys, anything in `/data`, CSV logs.
Check with `git status` that only intended files are staged — stage files
explicitly by path, never `git add -A`.

## Commit messages

Conventional-commit style, imperative, concrete to this codebase:

```
<type>(<scope>): <what changed>

<why, 1–3 lines: the problem and the chosen approach>

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

Scopes used in this project: `server`, `proxy`, `registry`, `api`, `weblogs`,
`celery`, `events`, `cli`, `docker`, `deploy`, `config`.
Examples:
- `feat(server): limit tunnels to 3 per SSH connection`
- `fix(proxy): route Host header case-insensitively`
- `refactor(registry): extract lifetime bookkeeping into TunnelLease`

## Pull requests

- Title = the main commit's first line.
- Body: **What** (2–4 lines), **Why**, **How tested** (exact commands +
  results, including a manual tunnel check via `ssh -R` when the tunnel path
  is touched), **Breaking changes** for the Frontend team (API/WebSocket) —
  write "None" explicitly if none.
- Create via `gh pr create` against `main`. End the body with:
  Mainted with Claude Code controller <git user>
