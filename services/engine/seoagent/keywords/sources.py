"""Free, live keyword sources.

Everything here reads public autocomplete endpoints — the same ones that power
the dropdown in each search box. Those suggestions come from what people are
actually typing right now, which is why this reflects current demand without
needing a paid keyword tool.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


@dataclass
class Suggestion:
    keyword: str
    source: str
    position: int


class SourceError(Exception):
    pass


def _get(url: str, params: dict, timeout: float) -> requests.Response:
    response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response


def _decode_jsonp_array(text: str) -> list:
    """Autocomplete endpoints sometimes wrap the array in a callback."""
    text = text.strip()
    if not text.startswith("["):
        match = re.search(r"\[.*\]", text, re.S)
        if not match:
            raise SourceError("پاسخ قابل تجزیه نبود")
        text = match.group(0)
    return json.loads(text)


def google_suggest(query: str, lang: str = "fa", country: str = "IR", timeout: float = 12.0) -> list[Suggestion]:
    """Google autocomplete. `client=firefox` returns a plain ["q", [...]] array."""
    response = _get(
        "https://suggestqueries.google.com/complete/search",
        {"client": "firefox", "q": query, "hl": lang, "gl": country},
        timeout,
    )
    data = _decode_jsonp_array(response.text)
    items = data[1] if len(data) > 1 and isinstance(data[1], list) else []
    return [Suggestion(str(s), "google", i) for i, s in enumerate(items) if isinstance(s, str)]


def youtube_suggest(query: str, lang: str = "fa", country: str = "IR", timeout: float = 12.0) -> list[Suggestion]:
    """YouTube autocomplete — skews towards how-to and tutorial intent."""
    response = _get(
        "https://suggestqueries.google.com/complete/search",
        {"client": "firefox", "ds": "yt", "q": query, "hl": lang, "gl": country},
        timeout,
    )
    data = _decode_jsonp_array(response.text)
    items = data[1] if len(data) > 1 and isinstance(data[1], list) else []
    return [Suggestion(str(s), "youtube", i) for i, s in enumerate(items) if isinstance(s, str)]


def bing_suggest(query: str, lang: str = "fa", country: str = "IR", timeout: float = 12.0) -> list[Suggestion]:
    response = _get("https://api.bing.com/osjson.aspx", {"query": query, "language": lang}, timeout)
    data = _decode_jsonp_array(response.text)
    items = data[1] if len(data) > 1 and isinstance(data[1], list) else []
    return [Suggestion(str(s), "bing", i) for i, s in enumerate(items) if isinstance(s, str)]


def duckduckgo_suggest(query: str, lang: str = "fa", country: str = "IR", timeout: float = 12.0) -> list[Suggestion]:
    response = _get(
        "https://duckduckgo.com/ac/",
        {"q": query, "type": "list", "kl": f"{country.lower()}-{lang.lower()}"},
        timeout,
    )
    data = _decode_jsonp_array(response.text)
    items: list[str] = []
    if data and isinstance(data[-1], list):
        items = [s for s in data[-1] if isinstance(s, str)]
    elif isinstance(data, list):
        items = [d.get("phrase", "") for d in data if isinstance(d, dict)]
    return [Suggestion(s, "duckduckgo", i) for i, s in enumerate(items) if s]


SOURCES = {
    "google": google_suggest,
    "youtube": youtube_suggest,
    "bing": bing_suggest,
    "duckduckgo": duckduckgo_suggest,
}


def trending_now(country: str = "IR", timeout: float = 12.0) -> list[str]:
    """Google Trends' public RSS feed of what is spiking in a country today."""
    try:
        response = _get("https://trends.google.com/trending/rss", {"geo": country.upper()}, timeout)
        root = ET.fromstring(response.text)
        titles = [item.findtext("title", "").strip() for item in root.iter("item")]
        return [t for t in titles if t][:25]
    except (requests.RequestException, ET.ParseError):
        return []


def related_from_wikipedia(query: str, lang: str = "fa", timeout: float = 12.0) -> list[str]:
    """Entity names around the topic — useful for semantic/entity coverage."""
    try:
        response = _get(
            f"https://{lang}.wikipedia.org/w/api.php",
            {
                "action": "opensearch",
                "search": query,
                "limit": "15",
                "namespace": "0",
                "format": "json",
            },
            timeout,
        )
        data = response.json()
        return [s for s in (data[1] if len(data) > 1 else []) if isinstance(s, str)]
    except (requests.RequestException, ValueError, IndexError):
        return []
