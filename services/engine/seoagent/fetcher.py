"""HTTP layer: polite fetching, redirect tracking, robots.txt handling."""

from __future__ import annotations

import re
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from . import netguard
from .config import CrawlConfig
from .models import PageData

MAX_BODY_BYTES = 5_000_000  # don't pull huge non-HTML payloads into memory

# <meta charset="..."> or <meta http-equiv="content-type" content="...; charset=...">
_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.I
)


def decode_body(body: bytes, content_type: str, response: Any = None) -> str:
    """Decode a response body, preferring the document's own declaration.

    `requests` follows RFC 2616 and falls back to ISO-8859-1 for `text/*` when
    the header carries no charset. Plenty of servers send exactly that while the
    document declares UTF-8 in a meta tag — trusting the header there turns
    Persian text into mojibake, so the in-document declaration wins.
    """
    header_charset = ""
    if "charset=" in content_type.lower():
        header_charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip(" \"'")

    candidates: list[str] = []
    if header_charset:
        candidates.append(header_charset)
    match = _META_CHARSET.search(body[:4096])
    if match:
        candidates.append(match.group(1).decode("ascii", errors="ignore"))
    if response is not None and getattr(response, "apparent_encoding", None):
        candidates.append(response.apparent_encoding)
    candidates.append("utf-8")

    for encoding in candidates:
        if not encoding:
            continue
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


@dataclass
class FetchResult:
    status_code: int
    final_url: str
    redirect_chain: list[tuple[int, str]]
    headers: dict[str, str]
    text: str
    byte_size: int
    elapsed_ms: int
    content_type: str
    error: str | None = None


def normalize_url(url: str, drop_fragment: bool = True) -> str:
    """Canonicalise a URL enough that we don't crawl the same page twice."""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    # strip the default port
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parsed.path or "/"
    fragment = "" if drop_fragment else parsed.fragment
    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, fragment))


def same_site(url: str, origin: str, follow_subdomains: bool = False) -> bool:
    host = urlparse(url).netloc.lower()
    origin_host = urlparse(origin).netloc.lower()
    if host == origin_host:
        return True
    if not follow_subdomains:
        # www and the bare domain are the same site for our purposes
        return host.removeprefix("www.") == origin_host.removeprefix("www.")
    base = origin_host.removeprefix("www.")
    return host == base or host.endswith("." + base)


def registrable_origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


class Fetcher:
    """Thread-safe requests wrapper with per-host rate limiting."""

    def __init__(self, config: CrawlConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
            }
        )
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._robots: urllib.robotparser.RobotFileParser | None = None
        self._robots_loaded = False
        self.robots_txt: str | None = None
        self.robots_status = 0

    # ---------------------------------------------------------------- polite

    def _throttle(self) -> None:
        if self.config.delay <= 0:
            return
        with self._lock:
            wait = self.config.delay - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    # ---------------------------------------------------------------- robots

    def load_robots(self, origin: str) -> None:
        if self._robots_loaded:
            return
        self._robots_loaded = True
        robots_url = urljoin(origin, "/robots.txt")
        try:
            resp = self.session.get(robots_url, timeout=self.config.timeout)
            self.robots_status = resp.status_code
            if resp.status_code == 200 and "html" not in resp.headers.get("Content-Type", ""):
                self.robots_txt = resp.text[:200_000]
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(self.robots_txt.splitlines())
                self._robots = parser
        except requests.RequestException:
            self.robots_status = 0

    def allowed(self, url: str) -> bool:
        if not self.config.respect_robots or self._robots is None:
            return True
        try:
            return self._robots.can_fetch(self.config.user_agent, url)
        except Exception:
            return True

    def robots_sitemaps(self) -> list[str]:
        if not self.robots_txt:
            return []
        found = []
        for line in self.robots_txt.splitlines():
            if line.strip().lower().startswith("sitemap:"):
                value = line.split(":", 1)[1].strip()
                if value:
                    found.append(value)
        return found

    # ----------------------------------------------------------------- fetch

    def fetch(self, url: str, method: str = "GET") -> FetchResult:
        started = time.monotonic()

        # Checked here rather than only on the submitted URL: a crawl follows
        # links and redirects, so an external page can steer us at the private
        # network unless every single request goes through the guard.
        try:
            netguard.check_url(url)
        except netguard.TargetNotAllowed as exc:
            return self._error(url, started, str(exc))

        self._throttle()
        try:
            resp = self.session.request(
                method,
                url,
                timeout=self.config.timeout,
                allow_redirects=True,
                stream=True,
            )
        except requests.TooManyRedirects:
            return self._error(url, started, "redirect loop / too many redirects")
        except requests.Timeout:
            return self._error(url, started, f"timeout after {self.config.timeout:.0f}s")
        except requests.RequestException as exc:
            return self._error(url, started, f"{type(exc).__name__}: {exc}")

        content_type = resp.headers.get("Content-Type", "")
        chain = [(r.status_code, r.url) for r in resp.history]

        text = ""
        size = 0
        if method != "HEAD":
            try:
                body = resp.raw.read(MAX_BODY_BYTES, decode_content=True) or b""
            except Exception as exc:
                resp.close()
                return self._error(url, started, f"read error: {exc}")
            size = len(body)
            if "html" in content_type.lower() or "xml" in content_type.lower() or not content_type:
                text = decode_body(body, content_type, resp)
        resp.close()

        return FetchResult(
            status_code=resp.status_code,
            final_url=resp.url,
            redirect_chain=chain,
            headers={k.lower(): v for k, v in resp.headers.items()},
            text=text,
            byte_size=size,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            content_type=content_type,
        )

    def _error(self, url: str, started: float, message: str) -> FetchResult:
        return FetchResult(
            status_code=0,
            final_url=url,
            redirect_chain=[],
            headers={},
            text="",
            byte_size=0,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            content_type="",
            error=message,
        )

    def check_status(self, url: str) -> tuple[int, str | None]:
        """Cheap liveness probe used for external link checking."""
        result = self.fetch(url, method="HEAD")
        if result.error or result.status_code in (405, 403, 0):
            # Plenty of servers reject HEAD; fall back to a ranged GET.
            result = self.fetch(url, method="GET")
        return result.status_code, result.error


def result_to_page(url: str, result: FetchResult, depth: int = 0) -> PageData:
    return PageData(
        url=url,
        final_url=result.final_url,
        status_code=result.status_code,
        redirect_chain=result.redirect_chain,
        elapsed_ms=result.elapsed_ms,
        content_type=result.content_type,
        headers=result.headers,
        error=result.error,
        html=result.text,
        byte_size=result.byte_size,
        depth=depth,
    )
