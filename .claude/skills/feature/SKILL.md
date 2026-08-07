---
name: feature
description: Full development pipeline for a feature or bugfix — runs the agent hierarchy Planning (Opus 5) → Coding (Opus 4.8) → Review (Opus 5) → Refactor (Opus 4.8). Use for any non-trivial change. Args = task description.
---

# Feature pipeline

Run the agent hierarchy for the task given in the arguments. Reply to the user
in the same language as their request (Russian → Russian, English → English).

## Steps

0. **Architecture (only for large/structural tasks)** — if the task changes
   data flow, adds a component, or touches the Registry/proxy/API topology,
   first launch the `architect` agent. Relay its chosen option + alternatives
   briefly to the user, then pass the chosen constraints to `planner`.
   Skip for ordinary features and fixes.
1. **Plan** — launch the `planner` agent (synchronously) with the task.
   Relay the plan to the user in 3–6 lines.
2. **Code** — launch the `coder` agent with the full plan text. It must
   implement the plan and run `uv run pytest`.
3. **Review** — launch the `reviewer` agent on the resulting diff.
   - If verdict is NEEDS FIXES: send the blocking findings back to the same
     `coder` agent (SendMessage — keep its context), then review again.
     Maximum 2 fix cycles; after that report the remaining findings to the
     user and stop.
4. **Refactor** — only after APPROVE, launch the `refactorer` agent on the
   changed files. Skip this step for trivial diffs (< ~20 lines).
5. **Report** — final summary to the user: what was built, files changed,
   test results, review verdict. Concrete, no fluff.

## Rules
- Agents run one at a time — each stage depends on the previous.
- Never skip Review for security-sensitive code (auth, subdomain validation,
  proxy, SSH sessions).
- Do not commit unless the user asked for it.
