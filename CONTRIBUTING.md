# Contributing to PublicHost

Thanks for helping! Bug reports, docs fixes and pull requests are all welcome.

## Development setup

You need [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync                        # dependencies + dev tools
cp .env.example .env           # local settings (Redis disabled)
uv run python -m publichost    # SSH server :2222 + HTTP proxy :8080
```

In another terminal:

```bash
python3 -m http.server 3000
ssh -p 2222 -R 0:localhost:3000 localhost
curl -H "Host: <label>.publichost.run" http://localhost:8080/
```

## Before opening a pull request

```bash
uv run ruff format
uv run ruff check
uv run pytest -q --cov
```

CI runs the same checks plus `shellcheck` on `deploy.sh` and a Docker build.

## Guidelines

- Keep changes focused: one pull request, one topic.
- The server is fully asyncio: no blocking calls on the event loop
  (use `asyncio.to_thread` with a short justification if unavoidable).
- Type hints everywhere and `from __future__ import annotations` in every
  module.
- Add or update tests for behavior changes.
- This is a public-facing service. Never give clients shell/exec/sftp, never
  log tunnel traffic payloads, and never commit keys, `.env*` or `secrets/`.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/),
  e.g. `fix(proxy): route Host header case-insensitively`.

More background on the architecture is in [AGENTS.md](AGENTS.md). By
participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Releases

Maintainers add the release to [CHANGELOG.md](CHANGELOG.md), bump `version` in
`pyproject.toml`, merge to `main` and push a tag:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

The `Release` workflow then publishes `ghcr.io/publichost-run/publichost`
images (`1.0.0`, `1.0`, `latest`; `main` publishes `edge`) and creates the
GitHub release with generated notes.

## Reporting security issues

Please do **not** open a public issue. See [SECURITY.md](SECURITY.md).
