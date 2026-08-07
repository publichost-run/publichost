---
name: git-flow
description: Prepare a git branch and commit message, and perform commit/push only after explicit user confirmation
whenToUse: When the user asks for a branch name and commit message, or wants to commit/push changes
---

# Git flow

This skill is invoked with a branch name and a commit message, for example:
`git-flow --branch feat/env-config --message "feat: add env config"`.

## Steps

1. Check `git status --short` to see what changed.
2. Suggest a concise English commit message that describes the change.
3. If the user provided a branch name, create and switch to it:
   `git checkout -b <branch>`.
4. Stage relevant files: `git add -A` or a specific set of files.
5. **Do not run `git commit` or `git push` without explicit user confirmation.**
   Show the proposed commit message and the list of staged files, then ask
   whether to proceed.
6. Only after the user confirms, commit with the agreed message and push if
   requested.

## Commit message style

- Use English.
- Use the conventional commits format:
  - `feat:` for new features
  - `fix:` for bug fixes
  - `refactor:` for code refactoring
  - `docs:` for documentation-only changes
  - `chore:` for tooling/config changes
- Keep the subject line under 72 characters.
- Add a blank line and a bullet list body if the change has multiple parts.

## Safety rules

- Never force-push, reset, rebase, or amend history unless explicitly asked.
- Never commit secrets, `.env`, `host_key`, or build artifacts.
- Ensure `ruff check` and `pytest` pass before committing.
