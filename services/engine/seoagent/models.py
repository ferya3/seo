"""Core data structures shared by the crawler, the rule engine and the report."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Severity(enum.Enum):
    """How badly an issue hurts rankings. Ordered from worst to mildest."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def weight(self) -> int:
        return _SEVERITY_WEIGHT[self]

    @property
    def label_fa(self) -> str:
        return _SEVERITY_FA[self]

    def __lt__(self, other: Severity) -> bool:
        return _SEVERITY_ORDER[self] < _SEVERITY_ORDER[other]


_SEVERITY_WEIGHT = {
    Severity.CRITICAL: 30,
    Severity.HIGH: 15,
    Severity.MEDIUM: 7,
    Severity.LOW: 3,
    Severity.INFO: 0,
}

_SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}

_SEVERITY_FA = {
    Severity.CRITICAL: "بحرانی",
    Severity.HIGH: "زیاد",
    Severity.MEDIUM: "متوسط",
    Severity.LOW: "کم",
    Severity.INFO: "اطلاعی",
}


class Category(enum.Enum):
    """Audit areas. These map 1:1 to the score cards on the dashboard."""

    INDEXING = "indexing"
    CONTENT = "content"
    TECHNICAL = "technical"
    PERFORMANCE = "performance"
    STRUCTURED_DATA = "structured_data"
    LINKS = "links"
    IMAGES = "images"
    EEAT = "eeat"
    INTERNATIONAL = "international"
    AI_SEARCH = "ai_search"

    @property
    def label_fa(self) -> str:
        return _CATEGORY_FA[self]

    @property
    def label_en(self) -> str:
        return _CATEGORY_EN[self]


_CATEGORY_FA = {
    Category.INDEXING: "ایندکس‌شدن و خزش",
    Category.CONTENT: "محتوا و کلمات کلیدی",
    Category.TECHNICAL: "فنی",
    Category.PERFORMANCE: "سرعت و Core Web Vitals",
    Category.STRUCTURED_DATA: "داده ساختاریافته",
    Category.LINKS: "لینک‌سازی",
    Category.IMAGES: "تصاویر",
    Category.EEAT: "اعتبار و E-E-A-T",
    Category.INTERNATIONAL: "چندزبانه",
    Category.AI_SEARCH: "جستجوی هوش مصنوعی",
}

_CATEGORY_EN = {
    Category.INDEXING: "Indexing & Crawling",
    Category.CONTENT: "Content & Keywords",
    Category.TECHNICAL: "Technical",
    Category.PERFORMANCE: "Speed & Core Web Vitals",
    Category.STRUCTURED_DATA: "Structured Data",
    Category.LINKS: "Linking",
    Category.IMAGES: "Images",
    Category.EEAT: "Trust & E-E-A-T",
    Category.INTERNATIONAL: "Internationalization",
    Category.AI_SEARCH: "AI Search",
}


@dataclass
class ImageRef:
    src: str
    alt: str | None
    width: str | None = None
    height: str | None = None
    loading: str | None = None
    is_in_first_viewport: bool = False


@dataclass
class LinkRef:
    href: str          # absolute
    raw_href: str      # exactly as authored
    anchor: str
    rel: str = ""
    is_internal: bool = False

    @property
    def is_nofollow(self) -> bool:
        return "nofollow" in self.rel.lower()


@dataclass
class PageData:
    """Everything the rules need to know about one crawled URL."""

    url: str
    final_url: str = ""
    status_code: int = 0
    redirect_chain: list[tuple[int, str]] = field(default_factory=list)
    elapsed_ms: int = 0
    content_type: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    depth: int = 0

    # Raw payload
    html: str = ""
    byte_size: int = 0

    # Head
    title: str | None = None
    meta_description: str | None = None
    meta_robots: str = ""
    canonical: str | None = None
    viewport: str | None = None
    lang: str | None = None
    charset: str | None = None
    hreflang: list[tuple[str, str]] = field(default_factory=list)
    og: dict[str, str] = field(default_factory=dict)
    twitter: dict[str, str] = field(default_factory=dict)
    favicon: str | None = None

    # Body
    headings: list[tuple[int, str]] = field(default_factory=list)
    text: str = ""
    word_count: int = 0
    images: list[ImageRef] = field(default_factory=list)
    links: list[LinkRef] = field(default_factory=list)
    jsonld: list[dict[str, Any]] = field(default_factory=list)
    microdata_types: list[str] = field(default_factory=list)

    # Resource hints used by the performance rules
    render_blocking_scripts: int = 0
    render_blocking_styles: int = 0
    inline_script_bytes: int = 0
    inline_style_bytes: int = 0
    external_script_count: int = 0
    iframe_count: int = 0

    # Filled in after the crawl finishes
    inlinks: int = 0
    content_hash: str = ""

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type.lower()

    @property
    def is_ok(self) -> bool:
        return 200 <= self.status_code < 300 and self.error is None

    @property
    def h1s(self) -> list[str]:
        return [t for lvl, t in self.headings if lvl == 1]

    @property
    def is_noindex(self) -> bool:
        directives = self.meta_robots.lower()
        header = self.headers.get("x-robots-tag", "").lower()
        return "noindex" in directives or "noindex" in header

    @property
    def is_indexable(self) -> bool:
        return self.is_ok and self.is_html and not self.is_noindex


@dataclass
class Issue:
    """One finding. Titles stay in English (the standard SEO vocabulary),
    the explanation and the fix are written in Persian."""

    id: str
    title_en: str
    title_fa: str
    detail_fa: str
    fix_fa: str
    severity: Severity
    category: Category
    urls: list[str] = field(default_factory=list)
    evidence: str = ""
    docs: str = ""

    @property
    def affected_count(self) -> int:
        return len(self.urls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title_en": self.title_en,
            "title_fa": self.title_fa,
            "detail_fa": self.detail_fa,
            "fix_fa": self.fix_fa,
            "severity": self.severity.value,
            "severity_fa": self.severity.label_fa,
            "category": self.category.value,
            "category_fa": self.category.label_fa,
            "category_en": self.category.label_en,
            "urls": self.urls[:50],
            "affected_count": self.affected_count,
            "evidence": self.evidence,
            "docs": self.docs,
        }


@dataclass
class SiteContext:
    """Site-wide facts gathered before the rules run."""

    start_url: str
    origin: str = ""
    pages: list[PageData] = field(default_factory=list)
    robots_txt: str | None = None
    robots_status: int = 0
    sitemap_urls: list[str] = field(default_factory=list)
    sitemap_locs: list[str] = field(default_factory=list)
    sitemap_errors: list[str] = field(default_factory=list)
    llms_txt_found: bool = False
    security_txt_found: bool = False
    target_keywords: list[str] = field(default_factory=list)
    psi: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    # Outbound link probe results: (url, found_on, status, error)
    external_checked: int = 0
    broken_external: list[tuple[str, str, int, str | None]] = field(default_factory=list)

    @property
    def html_pages(self) -> list[PageData]:
        return [p for p in self.pages if p.is_ok and p.is_html]

    @property
    def indexable_pages(self) -> list[PageData]:
        return [p for p in self.pages if p.is_indexable]

    def page_by_url(self, url: str) -> PageData | None:
        for p in self.pages:
            if p.url == url or p.final_url == url:
                return p
        return None
