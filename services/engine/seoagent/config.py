"""Runtime configuration for the SEO agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Google has indexed with the smartphone Googlebot only since July 2024, so the
# mobile rendering of a page is the one that matters. We crawl as a mobile
# client by default.
UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Mobile Safari/537.36 SeoAgent/1.0 (+personal-seo-audit)"
)
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 SeoAgent/1.0 (+personal-seo-audit)"
)
UA_GOOGLEBOT_MOBILE = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)

USER_AGENTS = {
    "mobile": UA_MOBILE,
    "desktop": UA_DESKTOP,
    "googlebot": UA_GOOGLEBOT_MOBILE,
}


@dataclass
class CrawlConfig:
    start_url: str
    max_pages: int = 50
    max_depth: int = 3
    timeout: float = 20.0
    delay: float = 0.4               # politeness pause between requests, seconds
    workers: int = 4
    user_agent_key: str = "mobile"
    respect_robots: bool = True
    follow_subdomains: bool = False
    check_external_links: bool = True
    max_external_checks: int = 40
    target_keywords: list[str] = field(default_factory=list)
    include_psi: bool = False        # Google PageSpeed Insights lookup (field CWV data)
    psi_api_key: str | None = None

    @property
    def user_agent(self) -> str:
        return USER_AGENTS.get(self.user_agent_key, UA_MOBILE)


@dataclass
class KeywordConfig:
    seed: str
    lang: str = "fa"                 # hl parameter
    country: str = "IR"              # gl parameter
    max_keywords: int = 200
    depth: int = 2                   # 1 = seed only, 2 = seed + modifier expansion
    include_questions: bool = True
    include_alphabet: bool = True
    include_comparisons: bool = True
    sources: list[str] = field(default_factory=lambda: ["google", "youtube", "bing", "duckduckgo"])
    timeout: float = 12.0
    delay: float = 0.15


def anthropic_api_key() -> str | None:
    """The AI layer is strictly optional; everything works without it."""
    return os.environ.get("ANTHROPIC_API_KEY") or None


def psi_api_key() -> str | None:
    return os.environ.get("PAGESPEED_API_KEY") or os.environ.get("PSI_API_KEY") or None


def data_dir() -> Path:
    """Where finished reports are stored so they survive a restart.

    Defaults to ./data next to the project, which is what the systemd unit
    points at with a dedicated StateDirectory.
    """
    configured = os.environ.get("SEO_AGENT_DATA_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / "data"
