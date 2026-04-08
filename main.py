"""Bulk-add DNS rewrite rules to AdGuard Home from a remote domain list."""

import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REQUIRED_ENV = (
    "ADGUARD_URL",
    "ADGUARD_USER",
    "ADGUARD_PASS",
    "REWRITE_IP",
    "DOMAINS_URL",
)


def load_config() -> dict:
    """Read configuration from environment variables."""
    missing: list[str] = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    return {
        "adguard_url": os.environ["ADGUARD_URL"].rstrip("/"),
        "username": os.environ["ADGUARD_USER"],
        "password": os.environ["ADGUARD_PASS"],
        "rewrite_ip": os.environ["REWRITE_IP"],
        "domains_url": os.environ["DOMAINS_URL"],
        "dry_run": os.getenv("DRY_RUN", "false").lower() == "true",
        "workers": int(os.getenv("WORKERS", "10")),
    }


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------


def fetch_domains(url: str) -> set[str]:
    """Download a plain-text domain list (one per line, '#' comments allowed)."""
    log.info("Fetching domain list from %s", url)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    domains: set[str] = set()
    for line in resp.text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        domains.add(entry.lower())

    log.info("Loaded %d unique domains", len(domains))
    return domains


# ---------------------------------------------------------------------------
# AdGuard Home helpers
# ---------------------------------------------------------------------------


def create_session(base_url: str, username: str, password: str) -> requests.Session:
    """Authenticate against AdGuard Home and return a reusable session."""
    session = requests.Session()
    log.info("Logging in to AdGuard Home at %s", base_url)

    resp = session.post(
        f"{base_url}/control/login",
        json={"name": username, "password": password},
        timeout=10,
    )
    resp.raise_for_status()

    log.info("Authentication successful")
    return session


def get_existing_domains(
    session: requests.Session, base_url: str, rewrite_ip: str
) -> set[str]:
    """Return the set of domains that already have a rewrite rule for *rewrite_ip*."""
    resp = session.get(f"{base_url}/control/rewrite/list", timeout=10)
    resp.raise_for_status()

    existing: set[str] = set()
    for item in resp.json():
        domain = item.get("domain")
        answer = item.get("answer")
        if domain and answer == rewrite_ip:
            existing.add(domain.lower())

    log.info("Found %d existing rewrite rules for %s", len(existing), rewrite_ip)
    return existing


def add_rewrite(
    session: requests.Session,
    base_url: str,
    domain: str,
    rewrite_ip: str,
    dry_run: bool,
) -> tuple[bool, str]:
    """Add a single DNS rewrite rule. Returns (success, message)."""
    if dry_run:
        return True, f"[dry-run] {domain} -> {rewrite_ip}"

    try:
        resp = session.post(
            f"{base_url}/control/rewrite/add",
            json={"domain": domain, "answer": rewrite_ip},
            timeout=10,
        )
        if resp.status_code == 200:
            return True, domain
        return False, f"{domain} (HTTP {resp.status_code}: {resp.text})"
    except requests.RequestException as exc:
        return False, f"{domain} ({exc})"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    cfg = load_config()

    domains: set[str] = fetch_domains(cfg["domains_url"])
    session = create_session(cfg["adguard_url"], cfg["username"], cfg["password"])
    existing: set[str] = get_existing_domains(
        session,
        cfg["adguard_url"],
        cfg["rewrite_ip"],
    )

    to_add: list[str] = sorted(domains - existing)
    log.info("%d new rules to add", len(to_add))

    if not to_add:
        log.info("Nothing to do")
        return

    if cfg["dry_run"]:
        log.warning("Dry-run mode enabled -- no changes will be made")

    added = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=cfg["workers"]) as executor:
        futures = {
            executor.submit(
                add_rewrite,
                session,
                cfg["adguard_url"],
                d,
                cfg["rewrite_ip"],
                cfg["dry_run"],
            ): d
            for d in to_add
        }

        for future in as_completed(futures):
            ok, msg = future.result()
            if ok:
                added += 1
                log.info("Added: %s", msg)
            else:
                failed += 1
                log.error("Failed: %s", msg)

    log.info(
        "Done -- added: %d, failed: %d, skipped (existing): %d",
        added,
        failed,
        len(existing),
    )


if __name__ == "__main__":
    main()
