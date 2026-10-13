"""Bulk-add DNS rewrite rules to AdGuard Home from a remote domain list.

Fetches a plain-text domain list, diffs it against the rewrite rules already
configured in AdGuard Home, and adds the missing ones (each domain plus a
``*.domain`` wildcard) in parallel.

Exit codes:
    0 -- success (or nothing to do)
    1 -- network/API failure, or some rules failed to add
    2 -- invalid configuration
  130 -- interrupted (Ctrl-C)
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

USER_AGENT = (
    "adguard-dns-rewriter (+https://github.com/hatamiarash7/adguard-dns-rewriter)"
)

REQUIRED_ENV = (
    "ADGUARD_URL",
    "ADGUARD_USER",
    "ADGUARD_PASS",
    "REWRITE_IP",
    "DOMAINS_URL",
)

FETCH_TIMEOUT = 30  # seconds, for downloading the domain list
API_TIMEOUT = 10  # seconds, for AdGuard Home API calls

# An optional leading "*." followed by dotted labels: letters, digits and
# hyphens (never leading/trailing), max 63 chars per label, min 2 labels.
_DOMAIN_RE = re.compile(
    r"^(?:\*\.)?"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}$"
)


class ConfigError(Exception):
    """Raised when the environment configuration is missing or invalid."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """Runtime configuration, loaded from environment variables."""

    adguard_url: str
    username: str
    password: str
    rewrite_ip: str
    domains_url: str
    dry_run: bool = False
    workers: int = 10


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Read and validate configuration from environment variables.

    Raises :class:`ConfigError` when required variables are missing or any
    optional variable has an invalid value.
    """
    env = os.environ if env is None else env

    missing = [key for key in REQUIRED_ENV if not env.get(key, "").strip()]
    if missing:
        raise ConfigError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    rewrite_ip = env["REWRITE_IP"].strip()
    try:
        ipaddress.ip_address(rewrite_ip)
    except ValueError:
        raise ConfigError(
            f"REWRITE_IP is not a valid IP address: {rewrite_ip!r}"
        ) from None

    workers_raw = env.get("WORKERS", "").strip() or "10"
    try:
        workers = int(workers_raw)
    except ValueError:
        raise ConfigError(
            f"WORKERS must be a positive integer, got {workers_raw!r}"
        ) from None
    if workers < 1:
        raise ConfigError(f"WORKERS must be >= 1, got {workers}")

    return Config(
        adguard_url=env["ADGUARD_URL"].strip().rstrip("/"),
        username=env["ADGUARD_USER"],
        password=env["ADGUARD_PASS"],
        rewrite_ip=rewrite_ip,
        domains_url=env["DOMAINS_URL"].strip(),
        dry_run=env.get("DRY_RUN", "").strip().lower() in {"true", "1", "yes"},
        workers=workers,
    )


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------


def parse_domains(text: str) -> set[str]:
    """Parse a plain-text domain list into a set of normalized domains.

    Accepts one domain per line. Blank lines and ``#`` comments (full-line or
    trailing) are ignored. Lines in hosts-file format (``0.0.0.0 domain.tld``
    or ``::1 domain.tld``) are reduced to the hostname. Entries that do not
    look like valid domains are skipped with a warning.
    """
    domains: set[str] = set()
    invalid: list[str] = []

    for raw_line in text.splitlines():
        entry = raw_line.split("#", 1)[0].strip()  # drop comments
        if not entry:
            continue

        tokens = entry.split()
        if len(tokens) > 1:
            try:
                ipaddress.ip_address(tokens[0])
            except ValueError:
                invalid.append(entry)
                continue
            entry = tokens[-1]  # hosts-file style line: "<ip> <hostname>"

        entry = entry.lower().rstrip(".")
        if _DOMAIN_RE.match(entry):
            domains.add(entry)
        else:
            invalid.append(entry)

    if invalid:
        preview = ", ".join(sorted(invalid)[:5])
        log.warning(
            "Skipped %d invalid line(s): %s%s",
            len(invalid),
            preview,
            " ..." if len(invalid) > 5 else "",
        )

    log.info("Loaded %d unique domains", len(domains))
    return domains


def expand_wildcards(domains: set[str]) -> set[str]:
    """Return *domains* plus a ``*.domain`` wildcard for every plain domain."""
    expanded = set(domains)
    expanded.update(f"*.{d}" for d in domains if not d.startswith("*."))
    return expanded


def fetch_domains(url: str) -> set[str]:
    """Download a plain-text domain list and parse it."""
    log.info("Fetching domain list from %s", url)
    resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return parse_domains(resp.text)


# ---------------------------------------------------------------------------
# AdGuard Home helpers
# ---------------------------------------------------------------------------


def create_session(cfg: Config) -> requests.Session:
    """Authenticate against AdGuard Home and return a reusable session.

    The connection pool is sized to match the worker count so parallel adds
    do not exhaust the default pool and drop keep-alive connections.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    adapter = HTTPAdapter(pool_connections=cfg.workers, pool_maxsize=cfg.workers)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    log.info("Logging in to AdGuard Home at %s", cfg.adguard_url)
    resp = session.post(
        f"{cfg.adguard_url}/control/login",
        json={"name": cfg.username, "password": cfg.password},
        timeout=API_TIMEOUT,
    )
    resp.raise_for_status()

    log.info("Authentication successful")
    return session


def get_existing_domains(
    session: requests.Session, base_url: str, rewrite_ip: str
) -> set[str]:
    """Return domains that already have a rewrite rule pointing at *rewrite_ip*."""
    resp = session.get(f"{base_url}/control/rewrite/list", timeout=API_TIMEOUT)
    resp.raise_for_status()

    existing = {
        item["domain"].lower()
        for item in resp.json()
        if item.get("domain") and item.get("answer") == rewrite_ip
    }
    log.info("Found %d existing rewrite rules for %s", len(existing), rewrite_ip)
    return existing


def add_rewrite(
    session: requests.Session,
    base_url: str,
    domain: str,
    rewrite_ip: str,
    dry_run: bool,
) -> tuple[bool, str]:
    """Add a single DNS rewrite rule. Returns ``(success, message)``."""
    if dry_run:
        return True, f"{domain} -> {rewrite_ip}"

    try:
        resp = session.post(
            f"{base_url}/control/rewrite/add",
            json={"domain": domain, "answer": rewrite_ip},
            timeout=API_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, f"{domain} ({exc})"

    if resp.status_code == 200:
        return True, domain
    return False, f"{domain} (HTTP {resp.status_code}: {resp.text})"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(cfg: Config) -> int:
    """Execute the sync. Returns the process exit code."""
    if cfg.dry_run:
        log.warning("Dry-run mode enabled -- no changes will be made")

    domains = expand_wildcards(fetch_domains(cfg.domains_url))
    session = create_session(cfg)
    existing = get_existing_domains(session, cfg.adguard_url, cfg.rewrite_ip)

    to_add = sorted(domains - existing)
    skipped = len(domains & existing)
    log.info("%d new rule(s) to add, %d already present", len(to_add), skipped)

    if not to_add:
        log.info("Nothing to do")
        return 0

    added = 0
    failed = 0
    verb = "Would add" if cfg.dry_run else "Added"

    with ThreadPoolExecutor(max_workers=cfg.workers) as executor:
        futures = {
            executor.submit(
                add_rewrite, session, cfg.adguard_url, d, cfg.rewrite_ip, cfg.dry_run
            ): d
            for d in to_add
        }
        for future in as_completed(futures):
            ok, msg = future.result()
            if ok:
                added += 1
                log.info("%s: %s", verb, msg)
            else:
                failed += 1
                log.error("Failed: %s", msg)

    log.info(
        "Done -- added: %d, failed: %d, skipped (existing): %d",
        added,
        failed,
        skipped,
    )
    return 1 if failed else 0


def main() -> int:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        cfg = load_config()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        return run(cfg)
    except requests.RequestException as exc:
        log.error("Network or API error: %s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
