"""Shared test doubles for the adguard-dns-rewriter test suite."""

import requests


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error for url", response=self)


class FakeSession:
    """Stands in for requests.Session against a fake AdGuard Home API.

    ``rewrite_list`` is the JSON payload served for GET /control/rewrite/list.
    ``add_status_by_domain`` maps domains to non-200 status codes, and
    ``exc_domains`` makes the add call raise a ConnectionError instead.
    """

    def __init__(self, rewrite_list=None, add_status_by_domain=None, exc_domains=None):
        self.headers = {}
        self.mounts = []
        self.posts = []  # list of (url, kwargs)
        self.gets = []  # list of (url, kwargs)
        self.rewrite_list = rewrite_list or []
        self.add_status_by_domain = add_status_by_domain or {}
        self.exc_domains = exc_domains or set()

    def mount(self, prefix, adapter):
        self.mounts.append((prefix, adapter))

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/control/login"):
            return FakeResponse(json_data={"name": "admin"})
        if url.endswith("/control/rewrite/add"):
            domain = kwargs["json"]["domain"]
            if domain in self.exc_domains:
                raise requests.ConnectionError("connection refused")
            status = self.add_status_by_domain.get(domain, 200)
            return FakeResponse(
                status_code=status, text="boom" if status != 200 else ""
            )
        raise AssertionError(f"unexpected POST: {url}")

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if url.endswith("/control/rewrite/list"):
            return FakeResponse(json_data=self.rewrite_list)
        raise AssertionError(f"unexpected GET: {url}")


def added_domains(session):
    """Return the domains POSTed to /control/rewrite/add."""
    return [
        kwargs["json"]["domain"]
        for url, kwargs in session.posts
        if url.endswith("/control/rewrite/add")
    ]
