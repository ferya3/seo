"""Breadth-first site crawler that produces a :class:`SiteContext`."""

from __future__ import annotations

import re
from collections import Counter, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from .config import CrawlConfig
from .fetcher import (
    Fetcher,
    normalize_url,
    registrable_origin,
    result_to_page,
    same_site,
)
from .models import PageData, SiteContext
from .parser import parse_page

ProgressFn = Callable[[str, int, int], None]

SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp",
    ".css", ".js", ".mjs", ".json", ".xml", ".txt", ".pdf", ".zip", ".rar", ".gz",
    ".mp4", ".mp3", ".wav", ".avi", ".mov", ".woff", ".woff2", ".ttf", ".eot",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".dmg", ".exe", ".apk",
)


def _is_crawlable(url: str) -> bool:
    path = urlparse(url).path.lower()
    return not path.endswith(SKIP_EXTENSIONS)


class Crawler:
    def __init__(self, config: CrawlConfig, progress: ProgressFn | None = None):
        self.config = config
        self.fetcher = Fetcher(config)
        self.progress = progress or (lambda message, done, total: None)

    def _report(self, message: str, done: int, total: int) -> None:
        try:
            self.progress(message, done, total)
        except Exception:  # noqa: BLE001, RUF100, S110
            pass

    # --------------------------------------------------------------- sitemaps

    def discover_sitemaps(self, ctx: SiteContext) -> None:
        candidates = list(self.fetcher.robots_sitemaps())
        for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml", "/wp-sitemap.xml"):
            candidates.append(urljoin(ctx.origin, path))

        seen: set[str] = set()
        queue = deque(candidates)
        locs: list[str] = []
        found: list[str] = []

        while queue and len(seen) < 25:
            sitemap_url = normalize_url(queue.popleft())
            if sitemap_url in seen:
                continue
            seen.add(sitemap_url)
            result = self.fetcher.fetch(sitemap_url)
            if result.error or result.status_code != 200:
                continue
            body = result.text
            if not body or "<" not in body:
                continue
            if "html" in result.content_type.lower() and "<urlset" not in body and "<sitemapindex" not in body:
                # a 200 HTML page pretending to be a sitemap (soft 404)
                ctx.sitemap_errors.append(f"{sitemap_url} → HTML بازگرداند، نه XML")
                continue
            found.append(sitemap_url)
            if "<sitemapindex" in body:
                for child in re.findall(r"<loc>\s*(.*?)\s*</loc>", body, re.I | re.S):
                    queue.append(child.strip())
            else:
                for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", body, re.I | re.S):
                    locs.append(loc.strip())

        ctx.sitemap_urls = found
        # de-duplicate while keeping order
        ctx.sitemap_locs = list(dict.fromkeys(locs))

    def _probe_wellknown(self, ctx: SiteContext) -> None:
        for path, attr in (("/llms.txt", "llms_txt_found"), ("/.well-known/security.txt", "security_txt_found")):
            result = self.fetcher.fetch(urljoin(ctx.origin, path))
            ok = (
                result.status_code == 200
                and not result.error
                and "html" not in result.content_type.lower()
            )
            setattr(ctx, attr, ok)

    # ------------------------------------------------------------------ crawl

    def crawl(self) -> SiteContext:
        start = normalize_url(self.config.start_url)
        origin = registrable_origin(start)
        ctx = SiteContext(start_url=start, origin=origin, target_keywords=self.config.target_keywords)

        self._report("خواندن robots.txt", 0, self.config.max_pages)
        self.fetcher.load_robots(origin)
        ctx.robots_txt = self.fetcher.robots_txt
        ctx.robots_status = self.fetcher.robots_status

        self._report("پیدا کردن سایت‌مپ", 0, self.config.max_pages)
        self.discover_sitemaps(ctx)
        self._probe_wellknown(ctx)

        queued: set[str] = {start}
        frontier: deque[tuple[str, int]] = deque([(start, 0)])
        # Seed from the sitemap too, so we cover pages that aren't linked from the homepage.
        for loc in ctx.sitemap_locs[: self.config.max_pages]:
            normalized = normalize_url(loc)
            if normalized not in queued and same_site(normalized, origin, self.config.follow_subdomains):
                queued.add(normalized)
                frontier.append((normalized, 1))

        pages: list[PageData] = []
        blocked: list[str] = []

        with ThreadPoolExecutor(max_workers=max(1, self.config.workers)) as pool:
            while frontier and len(pages) < self.config.max_pages:
                batch: list[tuple[str, int]] = []
                while frontier and len(batch) < self.config.workers and len(pages) + len(batch) < self.config.max_pages:
                    url, depth = frontier.popleft()
                    if depth > self.config.max_depth:
                        continue
                    if not self.fetcher.allowed(url):
                        blocked.append(url)
                        continue
                    batch.append((url, depth))
                if not batch:
                    continue

                results = list(pool.map(lambda item: self.fetcher.fetch(item[0]), batch))
                for (url, depth), result in zip(batch, results, strict=True):
                    page = result_to_page(url, result, depth)
                    if page.is_html and page.html:
                        parse_page(page, origin, self.config.follow_subdomains)
                    pages.append(page)
                    self._report(f"خزش: {url}", len(pages), self.config.max_pages)

                    if depth >= self.config.max_depth or not page.is_ok:
                        continue
                    for link in page.links:
                        if not link.is_internal or not _is_crawlable(link.href):
                            continue
                        target = normalize_url(link.href)
                        if target in queued:
                            continue
                        queued.add(target)
                        frontier.append((target, depth + 1))

        ctx.pages = pages
        if blocked:
            ctx.notes.append(
                f"{len(blocked)} آدرس به‌خاطر robots.txt خزش نشد (نمونه: {blocked[0]})"
            )
        if frontier:
            ctx.notes.append(
                f"سقف {self.config.max_pages} صفحه‌ای پر شد؛ حداقل {len(frontier)} آدرس دیگر باقی مانده است."
            )

        self._compute_inlinks(ctx)
        if self.config.check_external_links:
            self._check_external_links(ctx, pool_size=self.config.workers)
        return ctx

    # -------------------------------------------------------------- post-pass

    def _compute_inlinks(self, ctx: SiteContext) -> None:
        counter: Counter[str] = Counter()
        for page in ctx.html_pages:
            for link in page.links:
                if link.is_internal:
                    counter[normalize_url(link.href)] += 1
        for page in ctx.pages:
            page.inlinks = counter.get(normalize_url(page.url), 0)

    def _check_external_links(self, ctx: SiteContext, pool_size: int) -> None:
        """Probe a bounded sample of outbound links so a huge site doesn't stall."""
        external: dict[str, str] = {}
        for page in ctx.html_pages:
            for link in page.links:
                if link.is_internal:
                    continue
                key = normalize_url(link.href)
                external.setdefault(key, page.url)
                if len(external) >= self.config.max_external_checks:
                    break
            if len(external) >= self.config.max_external_checks:
                break

        if not external:
            return

        self._report(f"بررسی {len(external)} لینک خارجی", len(ctx.pages), self.config.max_pages)
        targets = list(external.items())
        with ThreadPoolExecutor(max_workers=max(1, pool_size)) as pool:
            statuses = list(pool.map(lambda item: self.fetcher.check_status(item[0]), targets))

        ctx.external_checked = len(targets)
        ctx.broken_external = [
            (url, source, status, error)
            for (url, source), (status, error) in zip(targets, statuses, strict=True)
            if error or status >= 400 or status == 0
        ]
