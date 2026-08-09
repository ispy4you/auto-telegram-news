# Contributing to Auto Telegram News

First off, thanks for taking the time to contribute! This project is a hobby-scale, self-hosted
Telegram news bot, and it grows through people who use it, hit a rough edge, and fix it.

Issues and pull requests in Russian are perfectly welcome — the maintainer and much of the
audience are Russian-speaking. English is fine too. Pick whichever you're comfortable writing in.

## Ways to contribute

- **Report a bug** — open an [issue](../../issues/new/choose) with steps to reproduce.
- **Propose a feature** — open a feature request issue before writing code, so we can agree on
  the approach first. This avoids wasted work on a PR that doesn't fit the project's direction.
- **Fix a bug / implement a feature** — see the workflow below.
- **Improve docs** — READMEs, `.env.example` comments, docstrings. Small doc PRs are always
  welcome and don't need a prior issue.

## Project layout

```
app/
  main.py              FastAPI app entrypoint, scheduler + Telethon listener startup
  config.py            Settings (pydantic-settings, reads .env)
  database.py           SQLAlchemy engine/session, auto-migrations
  models.py / schemas.py
  services/            Business logic: dedup, AI generation, publishing, fetching
  web/routes/          FastAPI routers (admin UI + JSON endpoints)
  web/templates/        Jinja2 templates (Bootstrap 5)
  cli/                  One-off scripts, e.g. init_telegram_session
tests/                  pytest suite (unit tests, in-memory SQLite)
```

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # defaults work for running tests without real Telegram/AI credentials
```

Run the test suite:

```bash
pytest
```

Run the app locally (needs PostgreSQL or the SQLite fallback — see [README.md](README.md)):

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Pull request workflow

1. Fork the repo and create a branch off `main` (`git checkout -b fix/short-description`).
2. Keep the PR focused — one bug or one feature per PR. Smaller PRs get reviewed faster.
3. Add or update tests for the behavior you changed, when practical.
4. Make sure `pytest` passes locally before opening the PR — CI runs it automatically on every PR.
5. Write a clear PR description: what changed and why. Link the issue it closes with
   `Closes #123` if there is one.
6. Be responsive to review comments. If a change requires a design discussion, say so — it's fine
   to pause a PR and take the discussion to the linked issue.

## Code style

- Follow the style already used in the file you're editing. No enforced formatter/linter yet — if
  you'd like to propose one (e.g. `ruff`), open an issue first so we agree on the config.
- Prefer small, readable functions over clever one-liners.
- Don't add abstractions or config options for hypothetical future use cases — this is an MVP
  codebase; keep changes proportional to the problem being solved.
- New behavior should have a test; bug fixes should ideally include a regression test.

## Reporting security issues

Please **do not** open a public issue for security vulnerabilities (e.g. auth bypass, secret
leakage, injection). Instead, report privately as described in [SECURITY.md](SECURITY.md).

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating, you agree
to uphold it.
