"""Tests for main.py -- parsing, config, AdGuard helpers and the run flow."""

import logging

import pytest
import requests

import main
from conftest import FakeSession, added_domains


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_env(**overrides):
    env = {
        "ADGUARD_URL": "http://adguard.local:3000/",
        "ADGUARD_USER": "admin",
        "ADGUARD_PASS": "secret",
        "REWRITE_IP": "192.168.0.2",
        "DOMAINS_URL": "http://cdn.example.com/domains.txt",
    }
    env.update(overrides)
    return env


def make_config(**overrides):
    defaults = {
        "adguard_url": "http://adguard.local:3000",
        "username": "admin",
        "password": "secret",
        "rewrite_ip": "192.168.0.2",
        "domains_url": "http://cdn.example.com/domains.txt",
        "dry_run": False,
        "workers": 2,
    }
    defaults.update(overrides)
    return main.Config(**defaults)


def patch_run_deps(monkeypatch, domains, existing, session=None):
    """Replace the network helpers used by main.run() with fakes."""
    session = session if session is not None else FakeSession()
    monkeypatch.setattr(main, "fetch_domains", lambda url: set(domains))
    monkeypatch.setattr(main, "create_session", lambda cfg: session)
    monkeypatch.setattr(main, "get_existing_domains", lambda s, url, ip: set(existing))
    return session


# ---------------------------------------------------------------------------
# parse_domains
# ---------------------------------------------------------------------------


def test_parse_domains_basic_dedup_and_lowercase():
    text = "\n".join(
        [
            "# full-line comment",
            "",
            "ads.example.com",
            "Tracker.Example.NET",
            "ads.example.com",
            "   spaces.example.com   ",
        ]
    )
    assert main.parse_domains(text) == {
        "ads.example.com",
        "tracker.example.net",
        "spaces.example.com",
    }


def test_parse_domains_strips_inline_comments():
    assert main.parse_domains("telemetry.example.org  # inline comment") == {
        "telemetry.example.org"
    }


def test_parse_domains_hosts_file_format():
    text = "\n".join(
        [
            "0.0.0.0 ads.example.com",
            "127.0.0.1 localhost",
            "::1 tracker.example.net",
            "fe00::0 telemetry.example.org",
        ]
    )
    assert main.parse_domains(text) == {
        "ads.example.com",
        "tracker.example.net",
        "telemetry.example.org",
    }  # "localhost" (no dot) is skipped as invalid


def test_parse_domains_normalizes_fqdn_trailing_dot():
    assert main.parse_domains("example.com.") == {"example.com"}


def test_parse_domains_keeps_wildcards():
    assert main.parse_domains("*.example.com") == {"*.example.com"}


def test_parse_domains_skips_invalid_entries(caplog):
    text = "\n".join(
        [
            "https://example.com/ads",  # URL, not a domain
            "not a domain",  # multiple tokens, first is not an IP
            "192.168.1.1",  # bare IP address
            "-bad-.com",  # malformed label
            "valid.example.com",
        ]
    )
    with caplog.at_level(logging.WARNING):
        domains = main.parse_domains(text)
    assert domains == {"valid.example.com"}
    assert "Skipped 4 invalid line(s)" in caplog.text
    assert "https://example.com/ads" in caplog.text


def test_parse_domains_warning_preview_is_capped(caplog):
    invalid = [f"bad{i}.example.c0m" for i in range(10)]  # digits in TLD -> invalid
    with caplog.at_level(logging.WARNING):
        main.parse_domains("\n".join(invalid))
    assert "Skipped 10 invalid line(s)" in caplog.text
    assert "..." in caplog.text


# ---------------------------------------------------------------------------
# expand_wildcards
# ---------------------------------------------------------------------------


def test_expand_wildcards_adds_wildcard_for_plain_domain():
    assert main.expand_wildcards({"a.com"}) == {"a.com", "*.a.com"}


def test_expand_wildcards_does_not_double_wildcards():
    assert main.expand_wildcards({"*.a.com"}) == {"*.a.com"}


def test_expand_wildcards_mixed():
    assert main.expand_wildcards({"a.com", "*.b.com"}) == {
        "a.com",
        "*.a.com",
        "*.b.com",
    }


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_defaults():
    cfg = main.load_config(env=make_env())
    assert cfg == main.Config(
        adguard_url="http://adguard.local:3000",  # trailing slash stripped
        username="admin",
        password="secret",
        rewrite_ip="192.168.0.2",
        domains_url="http://cdn.example.com/domains.txt",
        dry_run=False,
        workers=10,
    )


def test_load_config_optional_values():
    cfg = main.load_config(env=make_env(WORKERS="4", DRY_RUN="YES"))
    assert cfg.workers == 4
    assert cfg.dry_run is True


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "Yes"])
def test_load_config_dry_run_truthy(value):
    assert main.load_config(env=make_env(DRY_RUN=value)).dry_run is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "junk"])
def test_load_config_dry_run_falsy(value):
    assert main.load_config(env=make_env(DRY_RUN=value)).dry_run is False


def test_load_config_missing_variables():
    with pytest.raises(main.ConfigError) as excinfo:
        main.load_config(env={})
    for name in main.REQUIRED_ENV:
        assert name in str(excinfo.value)


def test_load_config_blank_value_counts_as_missing():
    with pytest.raises(main.ConfigError, match="REWRITE_IP"):
        main.load_config(env=make_env(REWRITE_IP="   "))


def test_load_config_invalid_rewrite_ip():
    with pytest.raises(main.ConfigError, match="REWRITE_IP"):
        main.load_config(env=make_env(REWRITE_IP="not-an-ip"))


def test_load_config_accepts_ipv6_rewrite_ip():
    cfg = main.load_config(env=make_env(REWRITE_IP="fd00::1"))
    assert cfg.rewrite_ip == "fd00::1"


@pytest.mark.parametrize("value", ["abc", "0", "-3", "2.5"])
def test_load_config_invalid_workers(value):
    with pytest.raises(main.ConfigError, match="WORKERS"):
        main.load_config(env=make_env(WORKERS=value))


def test_load_config_empty_workers_falls_back_to_default():
    assert main.load_config(env=make_env(WORKERS=" ")).workers == 10


# ---------------------------------------------------------------------------
# fetch_domains
# ---------------------------------------------------------------------------


def test_fetch_domains_parses_response(monkeypatch):
    class FakeGetResponse:
        status_code = 200
        text = "# list\nexample.com\n0.0.0.0 tracker.example.net\n"

        def raise_for_status(self):
            pass

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeGetResponse()

    monkeypatch.setattr(main.requests, "get", fake_get)
    domains = main.fetch_domains("http://cdn.example.com/list.txt")

    assert domains == {"example.com", "tracker.example.net"}
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "http://cdn.example.com/list.txt"
    assert kwargs["headers"]["User-Agent"] == main.USER_AGENT
    assert kwargs["timeout"] == main.FETCH_TIMEOUT


def test_fetch_domains_propagates_http_error(monkeypatch):
    def fake_get(url, **kwargs):
        resp = main.requests.Response()
        resp.status_code = 404
        return resp

    monkeypatch.setattr(main.requests, "get", fake_get)
    with pytest.raises(requests.HTTPError):
        main.fetch_domains("http://cdn.example.com/missing.txt")


# ---------------------------------------------------------------------------
# AdGuard Home helpers
# ---------------------------------------------------------------------------


def test_create_session_login_and_pool_sizing(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(main.requests, "Session", lambda: fake)

    cfg = make_config(workers=7)
    session = main.create_session(cfg)

    assert session is fake
    assert fake.headers["User-Agent"] == main.USER_AGENT
    assert len(fake.mounts) == 2  # https:// + http:// adapters
    for _, adapter in fake.mounts:
        # _pool_maxsize is the constructor kwarg HTTPAdapter stores
        assert adapter._pool_maxsize == 7

    url, kwargs = fake.posts[0]
    assert url == "http://adguard.local:3000/control/login"
    assert kwargs["json"] == {"name": "admin", "password": "secret"}


def test_get_existing_domains_filters_by_answer():
    session = FakeSession(
        rewrite_list=[
            {"domain": "A.com", "answer": "192.168.0.2"},
            {"domain": "b.com", "answer": "10.0.0.1"},  # different IP
            {"answer": "192.168.0.2"},  # no domain
        ]
    )
    existing = main.get_existing_domains(session, "http://x", "192.168.0.2")
    assert existing == {"a.com"}


def test_add_rewrite_success():
    session = FakeSession()
    ok, msg = main.add_rewrite(session, "http://x", "a.com", "1.2.3.4", False)
    assert ok is True
    assert msg == "a.com"
    assert added_domains(session) == ["a.com"]


def test_add_rewrite_http_failure():
    session = FakeSession(add_status_by_domain={"a.com": 400})
    ok, msg = main.add_rewrite(session, "http://x", "a.com", "1.2.3.4", False)
    assert ok is False
    assert "HTTP 400" in msg


def test_add_rewrite_network_error():
    session = FakeSession(exc_domains={"a.com"})
    ok, msg = main.add_rewrite(session, "http://x", "a.com", "1.2.3.4", False)
    assert ok is False
    assert "connection refused" in msg


def test_add_rewrite_dry_run_makes_no_request():
    session = FakeSession()
    ok, msg = main.add_rewrite(session, "http://x", "a.com", "1.2.3.4", True)
    assert ok is True
    assert msg == "a.com -> 1.2.3.4"
    assert session.posts == []


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_adds_missing_rules_and_reports_skipped(monkeypatch, caplog):
    session = patch_run_deps(
        monkeypatch,
        domains={"example.com", "new.com"},
        existing={"example.com", "*.example.com"},
    )
    with caplog.at_level(logging.INFO):
        code = main.run(make_config())

    assert code == 0
    # only new.com and its wildcard are added; example.com pair is skipped
    assert sorted(added_domains(session)) == ["*.new.com", "new.com"]
    assert "2 new rule(s) to add, 2 already present" in caplog.text
    assert "skipped (existing): 2" in caplog.text


def test_run_returns_error_code_when_adds_fail(monkeypatch, caplog):
    failing = FakeSession(add_status_by_domain={"bad.com": 500, "*.bad.com": 500})
    patch_run_deps(
        monkeypatch,
        domains={"bad.com", "good.com"},
        existing=set(),
        session=failing,
    )
    with caplog.at_level(logging.INFO):
        code = main.run(make_config())
    assert code == 1
    # all four rules attempted: bad.com + wildcard fail, good.com + wildcard succeed
    assert len(added_domains(failing)) == 4
    assert "Failed: bad.com (HTTP 500" in caplog.text
    assert "failed: 1" in caplog.text or "failed: 2" in caplog.text


def test_run_returns_error_code_on_network_exception(monkeypatch):
    session = FakeSession(exc_domains={"bad.com"})
    patch_run_deps(monkeypatch, domains={"bad.com"}, existing=set(), session=session)
    assert main.run(make_config()) == 1


def test_run_nothing_to_do(monkeypatch, caplog):
    session = patch_run_deps(
        monkeypatch,
        domains={"example.com"},
        existing={"example.com", "*.example.com"},
    )
    with caplog.at_level(logging.INFO):
        code = main.run(make_config())

    assert code == 0
    assert added_domains(session) == []
    assert "Nothing to do" in caplog.text


def test_run_dry_run_makes_no_changes(monkeypatch, caplog):
    session = patch_run_deps(monkeypatch, domains={"new.com"}, existing=set())
    with caplog.at_level(logging.INFO):
        code = main.run(make_config(dry_run=True))

    assert code == 0
    assert added_domains(session) == []
    assert "Would add: new.com -> 192.168.0.2" in caplog.text
    assert "no changes will be made" in caplog.text


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_config_error_returns_2(monkeypatch, caplog):
    monkeypatch.setattr(main.os, "environ", {})
    with caplog.at_level(logging.ERROR):
        assert main.main() == 2
    assert "Missing required environment variables" in caplog.text


def test_main_delegates_to_run(monkeypatch):
    monkeypatch.setattr(main, "load_config", lambda env=None: make_config())
    monkeypatch.setattr(main, "run", lambda cfg: 0)
    assert main.main() == 0


def test_main_catches_network_errors(monkeypatch, caplog):
    monkeypatch.setattr(main, "load_config", lambda env=None: make_config())

    def boom(cfg):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(main, "run", boom)
    with caplog.at_level(logging.ERROR):
        assert main.main() == 1
    assert "no route to host" in caplog.text


def test_main_catches_keyboard_interrupt(monkeypatch):
    monkeypatch.setattr(main, "load_config", lambda env=None: make_config())

    def interrupt(cfg):
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "run", interrupt)
    assert main.main() == 130
