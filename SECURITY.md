# Security Policy

## Supported versions

This project doesn't yet follow a formal release/versioning scheme — the `main` branch is the
supported version. Security fixes land there.

## Reporting a vulnerability

If you find a security issue (auth bypass, secret leakage, injection, CSRF gap, etc.), please
**do not open a public issue**. Instead, report it privately using GitHub's
[private vulnerability reporting](../../security/advisories/new) for this repository, or email
the maintainer directly at the address on their [GitHub profile](https://github.com/ispy4you).

Please include:

- A description of the issue and its impact.
- Steps to reproduce (or a proof-of-concept).
- The affected file(s)/route(s), if known.

You should get an initial response within a few days. Once a fix is ready, we'll coordinate on
disclosure timing before any public write-up.

## Scope notes

This is a self-hosted app: each deployment holds its own Telegram credentials, AI Gateway keys,
and admin password in a local `.env` file. Most "security" issues that matter here are:

- Bugs that let an unauthenticated user reach admin-only routes or data.
- CSRF/session handling gaps.
- Ways secrets could leak into logs, templates, or error responses.
- SQL injection or path traversal (e.g. via the `/media/` route).

Issues that only apply to a misconfigured deployment (e.g. running with the default
`APP_SECRET_KEY`/`ADMIN_PASSWORD` in production) are already mitigated by the startup check in
`app/config.py`, but are still worth flagging if you find a way around it.
