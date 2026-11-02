# Security policy

PublicHost exposes an SSH server and an HTTP proxy to the internet, so we take
security reports seriously.

## Reporting a vulnerability

Report privately through GitHub:
**Security → Report a vulnerability**
(<https://github.com/publichost-run/publichost-backend/security/advisories/new>).

Please include the affected version or commit, steps to reproduce, and the
impact you expect. We aim to acknowledge reports within 3 working days and to
publish a fix and an advisory once it is available. Please give us a
reasonable time to fix the issue before disclosing it publicly.

## Supported versions

Security fixes land on `main`. Self-hosters should update with
`./deploy.sh update`.

## Scope

In scope: the SSH server, the HTTP reverse proxy, the API and WebLogs viewer,
`deploy.sh` and the Docker/Traefik configuration in this repository.
Out of scope: issues in third-party services (Cloudflare, Let's Encrypt) and
in instances operated by other people.
