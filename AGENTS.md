# AGENTS.md

Single-file Python CLI (`main.py`) that bulk-adds DNS rewrite rules to a live AdGuard Home instance from a remote domain list. Plus a `tests/` suite (pytest) and GitHub Actions CI. Not an installable package (no build config; `pyproject.toml` only holds tool config).

## Commands

```bash
make install            # pip install -r requirements.txt (pinned)
make install-dev        # runtime + dev deps (pytest, flake8)
make check              # flake8 + pytest — run before submitting changes
make dry-run            # DRY_RUN=true, no changes — use this for live verification
make run                # live run — makes real changes to AdGuard Home
```

- The Makefile **auto-loads `.env`** (simple `KEY=value` lines) and exports it to recipes, so `make run` works without `set -a; source .env; set +a`. Values with `$`, quotes, or `export ` prefixes are not supported by this mechanism.
- Makefile uses `python3` by default; override with `PYTHON=.venv/bin/python`. Local venv is Python 3.13 (README requires 3.10+).
- To verify changes: `make check` (lint + 48 unit tests, no network needed). `make dry-run` against a disposable env for end-to-end checks.
- flake8 config: `.flake8` (`E501` ignored — long lines are fine). pytest config: `pyproject.toml` (`pythonpath = ["."]` so tests can `import main`).

## Behavior gotchas (main.py)

- Default mode is destructive: without `DRY_RUN=true`, it immediately POSTs new rules to the AdGuard Home API in parallel (`WORKERS`, default 10). Exit codes: 0 ok, 1 network/add failures, 2 bad config, 130 interrupted.
- Each domain is also added as `*.domain` — wildcard subdomains are always generated.
- De-dup only counts existing rules whose `answer` equals `REWRITE_IP`; domains rewritten to a different IP are re-added.
- Domain list parsing is forgiving: lowercases, de-dupes, strips `#` comments (full-line and trailing), extracts hostnames from hosts-file lines (`0.0.0.0 domain`), and skips invalid entries (URLs, bare IPs, single labels) with a warning.
- Config validation happens in `load_config()` (raises `ConfigError`); `main()` catches it and exits 2 — never call `sys.exit()` from helpers.

## Conventions

- Default branch: `main`. Conventional Commits (`feat:`, `fix:`, `docs:`, `chore(deps):`).
- `requirements.txt` / `requirements-dev.txt` are fully pinned and bot-managed (Renovate + Dependabot; minor/patch updates automerge).
- Style: 4-space indent, LF, final newline (`.editorconfig`); tabs in the Makefile; 2-space indent for YAML/JSON.
- CI: `.github/workflows/ci.yml` — flake8 + pytest on Python 3.10–3.13. Keep it green.
