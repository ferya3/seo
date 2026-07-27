"""Where search results come from.

An interface plus implementations, deliberately, because this is the one part
of the system that cannot be verified from here: every search endpoint is
blocked by this environment's proxy. Isolating it means the untested surface is
a single function per provider, and everything that consumes results —
position analysis, storage, events, orchestration — is tested against data of
a known shape.

**The HTML provider's selectors are written from the documented markup and have
NOT been run against a live response.** Validate it before trusting a ranking
report; `python -m services.serp.providers <query>` prints what it extracts.
Nothing else in this service depends on it being right: swap in a paid API
provider by registering another function in PROVIDERS.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from seoagent import netguard  # noqa: E402

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Mobile Safari/537.36"
)


class ProviderError(RuntimeError):
    """The provider could not produce results. Retryable."""


@dataclass(frozen=True)
class Result:
    position: int
    url: str
    domain: str
    title: str
    snippet: str = ""

    def to_dict(self) -> dict:
        return {
            "position": self.position,
            "url": self.url,
            "domain": self.domain,
            "title": self.title,
            "snippet": self.snippet,
        }


class Provider(Protocol):
    def __call__(
        self, query: str, lang: str = "fa", country: str = "IR", limit: int = 10
    ) -> list[Result]: ...


def domain_of(url: str) -> str:
    """The registrable-ish host, lowercased and without www.

    Not a public-suffix implementation: "shop.example.co.uk" stays whole. A
    ranking report compares hosts, and collapsing subdomains would merge a
    competitor's blog with their store.
    """
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _unwrap(href: str) -> str:
    """Search pages wrap outbound links in a redirector; the real URL is a
    query parameter. Reporting the redirector as the ranking URL would make
    every result look like it belonged to the search engine."""
    parsed = urlparse(href)
    if parsed.path.startswith("/l/") or "uddg" in (parsed.query or ""):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    if href.startswith("//"):
        return f"https:{href}"
    return href


# ------------------------------------------------------------------ providers


def duckduckgo_html(
    query: str, lang: str = "fa", country: str = "IR", limit: int = 10
) -> list[Result]:
    """Scrape the no-JavaScript results page.

    UNVERIFIED: not run against a live response from here. See module docstring.
    """

    url = "https://html.duckduckgo.com/html/"
    try:
        netguard.check_url(url)
        response = requests.post(
            url,
            data={"q": query, "kl": f"{country.lower()}-{lang.lower()}"},
            headers={"User-Agent": USER_AGENT, "Accept-Language": lang},
            timeout=20,
        )
        response.raise_for_status()
    except Exception as exc:
        raise ProviderError(f"duckduckgo: {type(exc).__name__}: {exc}") from exc

    return parse_duckduckgo(response.text, limit)


def parse_duckduckgo(html: str, limit: int = 10) -> list[Result]:
    """Split out from the fetch so the extraction can be tested without a
    network, and so a saved response can be replayed against it."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    results: list[Result] = []

    for block in soup.select("div.result, div.web-result"):
        link = block.select_one("a.result__a")
        if link is None or not link.get("href"):
            continue
        # Ads carry the same result markup; counting them as organic positions
        # would shift every real ranking down.
        if "result--ad" in (block.get("class") or []):
            continue

        url = _unwrap(str(link.get("href")))
        snippet = block.select_one(".result__snippet")
        results.append(Result(
            position=len(results) + 1,
            url=url,
            domain=domain_of(url),
            title=link.get_text(" ", strip=True),
            snippet=snippet.get_text(" ", strip=True) if snippet else "",
        ))
        if len(results) >= limit:
            break

    return results


PROVIDERS: dict[str, Callable[..., list[Result]]] = {
    "duckduckgo": duckduckgo_html,
}


def get(name: str) -> Callable[..., list[Result]]:
    provider = PROVIDERS.get(name)
    if provider is None:
        raise ProviderError(f"unknown provider {name!r}; have {sorted(PROVIDERS)}")
    return provider


def default_name() -> str:
    return os.environ.get("SERP_PROVIDER", "duckduckgo")


if __name__ == "__main__":  # pragma: no cover - the manual check for the above
    logging.basicConfig(level=logging.INFO)
    term = " ".join(sys.argv[1:]) or "کفش ورزشی"
    for result in get(default_name())(term):
        print(f"{result.position:2}. {result.domain:30} {result.title[:60]}")
