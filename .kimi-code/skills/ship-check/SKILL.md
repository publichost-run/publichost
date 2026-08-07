---
name: ship-check
description: Pre-commit or pre-push checks — lint, formatting, tests, repository cleanliness
whenToUse: When the user asks to check code before committing, prepare a PR, or make sure everything is green
---

# Ship-check

Run in order. Do not skip red output and do not suggest committing until
everything is green:

1. `uv run ruff check` — lint.
2. `uv run ruff format --check` — formatting. If red: `uv run ruff format`,
   then re-check.
3. `uv run pytest -q` — tests.
4. `git status --short` — changes must not include `host_key`, `.env`,
   `__pycache__`, `.venv` (all are covered by .gitignore; if they appear, stop
   and investigate).
5. Check against AGENTS.md: code is in English, type hints are present, no
   unrelated changes outside the task.

Git mutations (commit/push) only on explicit user request, with confirmation
each time.
