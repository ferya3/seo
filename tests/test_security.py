"""SSRF guard and dashboard authentication."""

from __future__ import annotations

import pytest

from seoagent import netguard
from seoagent.config import CrawlConfig
from seoagent.fetcher import Fetcher
from seoagent.web import auth
from seoagent.web.app import create_app


@pytest.fixture(autouse=True)
def block_private(monkeypatch):
    """These tests exercise the guard, so turn off the test-wide allowance."""
    monkeypatch.delenv("SEO_AGENT_ALLOW_PRIVATE", raising=False)
    netguard.reset_cache()
    yield
    netguard.reset_cache()


# ------------------------------------------------------------------ ssrf guard


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",   # AWS / Azure / DO metadata
        "http://metadata.google.internal/",           # GCP metadata
        "http://100.100.100.200/",                    # Alibaba metadata
        "http://127.0.0.1:8080/",                     # loopback
        "http://localhost/admin",                     # loopback by name
        "http://10.0.0.5/",                           # private
        "http://192.168.1.1/",                        # private
        "http://172.16.4.2/",                         # private
        "http://[::1]/",                              # loopback v6
        "http://0.0.0.0/",                            # unspecified
    ],
)
def test_internal_targets_are_refused(url):
    assert not netguard.is_allowed(url)
    with pytest.raises(netguard.TargetNotAllowed):
        netguard.check_url(url)


@pytest.mark.parametrize("url", ["https://example.com/", "http://93.184.216.34/"])
def test_public_targets_are_allowed(url):
    netguard.check_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/"])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(netguard.TargetNotAllowed):
        netguard.check_url(url)


def test_hostname_resolving_to_loopback_is_refused(monkeypatch):
    """A public-looking name whose DNS points inside must still be blocked."""
    monkeypatch.setattr(netguard, "_resolve", lambda host: ("127.0.0.1",))
    with pytest.raises(netguard.TargetNotAllowed):
        netguard.check_url("http://sneaky.example.com/")


def test_allow_private_opt_out_works(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_ALLOW_PRIVATE", "1")
    netguard.check_url("http://127.0.0.1:5000/")
    netguard.check_url("http://192.168.1.50/")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://100.100.100.200/",
        "http://anything.internal/",
    ],
)
def test_metadata_stays_blocked_even_with_allow_private(monkeypatch, url):
    """ALLOW_PRIVATE exists to audit a LAN staging site. That is not a reason
    to hand out the server's cloud IAM credentials."""
    monkeypatch.setenv("SEO_AGENT_ALLOW_PRIVATE", "1")
    with pytest.raises(netguard.TargetNotAllowed):
        netguard.check_url(url)


def test_dashboard_blocks_metadata_even_with_allow_private(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_ALLOW_PRIVATE", "1")
    client = create_app().test_client()
    response = client.post("/api/audit", json={"url": "http://169.254.169.254/"})
    assert response.status_code == 400


def test_fetcher_refuses_blocked_targets_without_a_request(monkeypatch):
    """The guard must sit in the fetcher, not only at submit time — a crawl
    follows links and redirects to wherever the page points."""
    fetcher = Fetcher(CrawlConfig(start_url="https://example.com", delay=0))

    def explode(*args, **kwargs):
        raise AssertionError("no HTTP request should be made for a blocked target")

    monkeypatch.setattr(fetcher.session, "request", explode)

    result = fetcher.fetch("http://169.254.169.254/latest/meta-data/")
    assert result.status_code == 0
    assert result.error and "مجاز نیست" in result.error


def test_dashboard_rejects_blocked_target_with_a_readable_error(monkeypatch):
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.post("/api/audit", json={"url": "http://169.254.169.254/"})
    assert response.status_code == 400
    assert "مجاز نیست" in response.get_json()["error"]


# ---------------------------------------------------------------------- auth


def test_auth_is_off_when_no_password_is_set(monkeypatch):
    monkeypatch.delenv("SEO_AGENT_PASSWORD", raising=False)
    assert not auth.is_enabled()

    client = create_app().test_client()
    assert client.get("/").status_code == 200


def test_password_protects_every_route(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_PASSWORD", "s3cret")
    client = create_app().test_client()

    for path in ("/", "/api/jobs", "/report/anything"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert "Basic" in response.headers["WWW-Authenticate"]


def test_healthz_stays_open_for_probes(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_PASSWORD", "s3cret")
    client = create_app().test_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_correct_credentials_are_accepted(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_PASSWORD", "s3cret")
    monkeypatch.setenv("SEO_AGENT_USERNAME", "saeed")
    client = create_app().test_client()

    assert client.get("/", auth=("saeed", "s3cret")).status_code == 200
    assert client.get("/", auth=("saeed", "wrong")).status_code == 401
    assert client.get("/", auth=("someone", "s3cret")).status_code == 401


def test_post_endpoints_are_protected_too(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_PASSWORD", "s3cret")
    client = create_app().test_client()
    assert client.post("/api/audit", json={"url": "https://example.com"}).status_code == 401


# ----------------------------------------------------- refusing to run open


def test_public_bind_without_a_password_is_refused(monkeypatch):
    monkeypatch.delenv("SEO_AGENT_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as exc:
        auth.require_auth_or_loopback("0.0.0.0")
    assert "SEO_AGENT_PASSWORD" in str(exc.value)


def test_public_bind_with_a_password_is_fine(monkeypatch):
    monkeypatch.setenv("SEO_AGENT_PASSWORD", "s3cret")
    auth.require_auth_or_loopback("0.0.0.0")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_never_needs_a_password(monkeypatch, host):
    monkeypatch.delenv("SEO_AGENT_PASSWORD", raising=False)
    auth.require_auth_or_loopback(host)
