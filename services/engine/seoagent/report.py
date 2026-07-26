"""Turn a crawl plus its issues into a scored, prioritised report."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import Category, Issue, Severity, SiteContext

# Weight of each category in the overall score. Categories with no signal at all
# (e.g. no hreflang anywhere) are dropped from the average instead of scoring 100.
CATEGORY_WEIGHT = {
    Category.INDEXING: 1.4,
    Category.CONTENT: 1.4,
    Category.TECHNICAL: 1.2,
    Category.PERFORMANCE: 1.2,
    Category.LINKS: 1.0,
    Category.STRUCTURED_DATA: 0.8,
    Category.IMAGES: 0.7,
    Category.EEAT: 1.0,
    Category.AI_SEARCH: 0.7,
    Category.INTERNATIONAL: 0.5,
}


@dataclass
class CategoryScore:
    category: Category
    score: int
    issue_count: int
    penalty: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "label_fa": self.category.label_fa,
            "label_en": self.category.label_en,
            "score": self.score,
            "issue_count": self.issue_count,
            "grade": grade_for(self.score),
        }


@dataclass
class Report:
    site: SiteContext
    issues: list[Issue]
    overall_score: int = 0
    category_scores: list[CategoryScore] = field(default_factory=list)
    generated_at: str = ""
    ai_suggestions: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_url": self.site.start_url,
            "generated_at": self.generated_at,
            "overall_score": self.overall_score,
            "grade": grade_for(self.overall_score),
            "stats": self.stats(),
            "category_scores": [c.to_dict() for c in self.category_scores],
            "issues": [i.to_dict() for i in self.issues],
            "quick_wins": [i.to_dict() for i in self.quick_wins()],
            "critical": [i.to_dict() for i in self.by_severity(Severity.CRITICAL)],
            "pages": [
                {
                    "url": p.url,
                    "status": p.status_code,
                    "title": p.title,
                    "words": p.word_count,
                    "inlinks": p.inlinks,
                    "depth": p.depth,
                    "indexable": p.is_indexable,
                    "load_ms": p.elapsed_ms,
                    # Lets consumers of page.updated skip work when nothing changed.
                    "content_hash": p.content_hash,
                }
                for p in self.site.pages
            ],
            "psi": self.site.psi,
            "notes": self.site.notes,
            "ai_suggestions": self.ai_suggestions,
        }

    def stats(self) -> dict[str, Any]:
        pages = self.site.pages
        html = self.site.html_pages
        indexable = self.site.indexable_pages
        words = [p.word_count for p in indexable] or [0]
        return {
            "pages_crawled": len(pages),
            "html_pages": len(html),
            "indexable_pages": len(indexable),
            "errors": len([p for p in pages if p.error or p.status_code >= 400]),
            "redirects": len([p for p in pages if p.redirect_chain]),
            "avg_words": int(sum(words) / len(words)),
            "avg_load_ms": int(sum(p.elapsed_ms for p in pages) / len(pages)) if pages else 0,
            "sitemap_urls": len(self.site.sitemap_locs),
            "has_robots": self.site.robots_txt is not None,
            "total_issues": len(self.issues),
            "critical_count": len(self.by_severity(Severity.CRITICAL)),
            "high_count": len(self.by_severity(Severity.HIGH)),
            "external_checked": self.site.external_checked,
        }

    def by_severity(self, severity: Severity) -> list[Issue]:
        return [i for i in self.issues if i.severity is severity]

    def by_category(self, category: Category) -> list[Issue]:
        return [i for i in self.issues if i.category is category]

    def quick_wins(self, limit: int = 8) -> list[Issue]:
        """High impact, low effort — what to fix first."""
        cheap = {
            "title-missing", "title-too-long", "title-too-short", "meta-description-missing",
            "h1-missing", "image-alt-missing", "viewport-missing", "canonical-missing",
            "robots-no-sitemap", "html-lang-missing", "og-missing", "image-no-dimensions",
            "no-lazy-loading", "sitemap-missing", "charset-missing", "missing-rtl-direction",
            "no-breadcrumb-schema", "empty-anchor-text", "url-uppercase", "no-hsts",
        }
        ranked = sorted(
            (i for i in self.issues if i.id in cheap or i.severity in (Severity.CRITICAL, Severity.HIGH)),
            key=lambda i: (i.severity, -i.affected_count),
        )
        return ranked[:limit]


def grade_for(score: int) -> str:
    if score >= 90:
        return "عالی"
    if score >= 75:
        return "خوب"
    if score >= 60:
        return "قابل قبول"
    if score >= 40:
        return "ضعیف"
    return "بحرانی"


def _penalty(issue: Issue, page_count: int) -> float:
    """Severity drives the base penalty; breadth scales it sub-linearly so one
    site-wide problem doesn't zero out a category on its own."""
    base = issue.severity.weight
    if base == 0:
        return 0.0
    affected = max(1, issue.affected_count)
    breadth = min(1.0, affected / max(1, page_count))
    # 0.55 floor: even a single-page critical issue costs most of its weight.
    return base * (0.55 + 0.45 * math.sqrt(breadth))


def build_report(ctx: SiteContext, issues: list[Issue], ai_suggestions: dict | None = None) -> Report:
    page_count = max(1, len(ctx.html_pages))

    penalties: dict[Category, float] = {c: 0.0 for c in Category}
    counts: dict[Category, int] = {c: 0 for c in Category}
    for issue in issues:
        penalties[issue.category] += _penalty(issue, page_count)
        if issue.severity is not Severity.INFO:
            counts[issue.category] += 1

    # A category is "in play" if any rule could have fired for it.
    active = _active_categories(ctx, issues)

    category_scores: list[CategoryScore] = []
    for category in Category:
        if category not in active:
            continue
        score = max(0, min(100, round(100 - penalties[category])))
        category_scores.append(CategoryScore(category, score, counts[category], penalties[category]))

    if category_scores:
        total_weight = sum(CATEGORY_WEIGHT[c.category] for c in category_scores)
        overall = round(
            sum(c.score * CATEGORY_WEIGHT[c.category] for c in category_scores) / total_weight
        )
    else:
        overall = 100

    # A critical issue anywhere caps the overall score — a site blocked from
    # indexing should never show a green light because everything else is fine.
    if any(i.severity is Severity.CRITICAL for i in issues):
        overall = min(overall, 45)

    category_scores.sort(key=lambda c: c.score)

    return Report(
        site=ctx,
        issues=issues,
        overall_score=overall,
        category_scores=category_scores,
        generated_at=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        ai_suggestions=ai_suggestions,
    )


def _active_categories(ctx: SiteContext, issues: list[Issue]) -> set[Category]:
    """Always score the core areas; score optional ones only when relevant."""
    active = {
        Category.INDEXING,
        Category.CONTENT,
        Category.TECHNICAL,
        Category.PERFORMANCE,
        Category.LINKS,
        Category.STRUCTURED_DATA,
        Category.EEAT,
        Category.AI_SEARCH,
    }
    if any(p.images for p in ctx.html_pages):
        active.add(Category.IMAGES)
    if any(p.hreflang for p in ctx.html_pages) or any(
        i.category is Category.INTERNATIONAL for i in issues
    ):
        active.add(Category.INTERNATIONAL)
    return active
