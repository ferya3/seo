"""End-to-end audit tests against the fixture sites."""

from __future__ import annotations

import pytest
from seoagent.config import CrawlConfig
from seoagent.crawler import Crawler
from seoagent.export import audit_markdown
from seoagent.models import Severity
from seoagent.report import build_report
from seoagent.rules import run_all


def audit(url: str, **overrides) -> tuple:
    config = CrawlConfig(
        start_url=url,
        max_pages=overrides.pop("max_pages", 20),
        max_depth=overrides.pop("max_depth", 3),
        delay=0,
        check_external_links=overrides.pop("check_external_links", False),
        **overrides,
    )
    ctx = Crawler(config).crawl()
    issues = run_all(ctx)
    return ctx, issues, build_report(ctx, issues)


@pytest.fixture(scope="module")
def bad_audit(bad_site):
    return audit(bad_site, target_keywords=["فروشگاه اینترنتی"])


@pytest.fixture(scope="module")
def good_audit(good_site):
    return audit(good_site)


def ids(issues) -> set[str]:
    return {issue.id for issue in issues}


# ------------------------------------------------------------------ crawling


def test_crawler_discovers_linked_pages(bad_audit):
    ctx, _, _ = bad_audit
    paths = {page.url.rsplit("/", 1)[-1] for page in ctx.pages}
    assert "about" in paths
    assert any("post-1" in p for p in paths)
    assert len(ctx.pages) >= 5


def test_crawler_follows_sitemap(good_audit):
    ctx, _, _ = good_audit
    assert ctx.sitemap_urls, "sitemap.xml should be discovered via robots.txt"
    assert len(ctx.sitemap_locs) == 4


def test_inlinks_are_counted(good_audit):
    ctx, _, _ = good_audit
    about = next(p for p in ctx.pages if p.url.endswith("/about.html"))
    assert about.inlinks >= 2


def test_parser_extracts_head_and_body(good_audit):
    ctx, _, _ = good_audit
    home = ctx.pages[0]
    assert home.title == "آموزش سئو تکنیکال | نمونه‌سایت"
    assert home.meta_description
    assert home.canonical
    assert home.lang == "fa"
    assert home.charset == "utf-8"
    assert home.h1s == ["آموزش سئو تکنیکال"]
    assert home.word_count > 30
    assert any("Organization" in str(block.get("@type")) for block in home.jsonld)
    assert home.images and home.images[0].alt


# --------------------------------------------------------------------- rules


@pytest.mark.parametrize(
    "issue_id",
    [
        "h1-empty",
        "h1-multiple",
        "heading-order",
        "title-duplicate",
        "meta-description-missing",
        "canonical-missing",
        "viewport-missing",
        "html-lang-missing",
        "charset-missing",
        "thin-content",
        "duplicate-content",
        "keyword-stuffing",
        "image-alt-missing",
        "image-no-dimensions",
        "image-filename",
        "generic-anchor-text",
        "js-navigation",
        "broken-internal-links",
        "url-underscores",
        "url-uppercase",
        "url-parameters",
        "no-structured-data",
        "og-missing",
        "missing-rtl-direction",
        "render-blocking-resources",
        "soft-404",
        "robots-no-sitemap",
        "sitemap-missing",
        "missing-contact-page",
        "no-llms-txt",
    ],
)
def test_bad_site_reports_expected_issue(bad_audit, issue_id):
    _, issues, _ = bad_audit
    assert issue_id in ids(issues), f"expected rule {issue_id} to fire"


def test_target_keyword_absence_is_reported(bad_audit):
    _, issues, _ = bad_audit
    assert any(i.id.startswith("keyword-absent-") for i in issues)


def test_good_site_avoids_the_basics(good_audit):
    _, issues, _ = good_audit
    must_not_fire = {
        "title-missing", "title-duplicate", "meta-description-missing",
        "h1-missing", "h1-multiple", "canonical-missing", "viewport-missing",
        "html-lang-missing", "charset-missing", "missing-rtl-direction",
        "no-structured-data", "og-missing", "image-alt-missing",
        "image-no-dimensions", "sitemap-missing", "robots-no-sitemap",
        "missing-about-page", "missing-contact-page", "no-organization-schema",
        "no-breadcrumb-schema", "no-social-profiles", "js-navigation",
        "broken-internal-links", "server-error", "site-wide-noindex",
    }
    assert not (must_not_fire & ids(issues)), sorted(must_not_fire & ids(issues))


def test_every_issue_is_fully_populated(bad_audit):
    _, issues, _ = bad_audit
    for issue in issues:
        assert issue.title_en and issue.title_fa, issue.id
        assert issue.detail_fa and issue.fix_fa, issue.id
        assert isinstance(issue.severity, Severity), issue.id


def test_a_broken_rule_does_not_sink_the_audit(bad_audit, monkeypatch):
    from seoagent.rules import base

    ctx, _, _ = bad_audit

    def exploding(_ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(base, "_REGISTRY", [("exploder", exploding), *base._REGISTRY[:3]])
    issues = base.run_all(ctx)
    assert isinstance(issues, list)
    assert any("exploder" in note for note in ctx.notes)


# -------------------------------------------------------------------- report


def test_scoring_ranks_the_good_site_higher(bad_audit, good_audit):
    _, _, bad_report = bad_audit
    _, _, good_report = good_audit
    assert good_report.overall_score > bad_report.overall_score
    assert 0 <= bad_report.overall_score <= 100
    assert 0 <= good_report.overall_score <= 100


def test_report_serialises_and_exports(bad_audit):
    _, _, report = bad_audit
    payload = report.to_dict()
    for key in ("overall_score", "category_scores", "issues", "stats", "pages", "quick_wins"):
        assert key in payload

    markdown = audit_markdown(payload)
    assert "# گزارش سئو" in markdown
    assert "## فهرست کامل ایرادها" in markdown
    assert len(markdown) > 500


def test_quick_wins_are_ordered_by_severity(bad_audit):
    _, _, report = bad_audit
    wins = report.quick_wins()
    severities = [w.severity for w in wins]
    assert severities == sorted(severities)


def test_the_report_carries_the_internal_link_graph(good_audit):
    """The page list says a page has N incoming links; only the edges say from
    where. Every internal-linking question is a question about edges, so the
    crawl records them once instead of each consumer re-crawling to rebuild the
    same graph."""
    _, _, report = good_audit
    links = report.to_dict()["links"]

    assert links["edges"], "a crawled site with navigation has internal edges"
    assert links["truncated"] is False
    first = links["edges"][0]
    assert set(first) == {"from", "to", "anchor", "nofollow"}


def test_external_links_stay_out_of_the_graph(good_audit):
    """They would roughly double the size and answer a different question."""
    ctx, _, report = good_audit
    hosts = {edge["to"].split("/")[2] for edge in report.to_dict()["links"]["edges"]}
    assert hosts <= {ctx.start_url.split("/")[2]}


def test_the_graph_says_when_it_was_cut_short(good_audit):
    """A partial graph makes every count a lower bound. Silently truncating
    would turn a big site into a wrong answer about orphan pages."""
    _, _, report = good_audit
    cut = report.internal_edges(limit=1)
    assert cut["truncated"] is True
    assert len(cut["edges"]) == 1
