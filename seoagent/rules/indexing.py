"""Indexability: noindex directives, soft 404s and crawl-budget waste."""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import urlparse

from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

SOFT_404_MARKERS = re.compile(
    r"(صفحه\s*(?:مورد نظر\s*)?(?:پیدا|یافت)\s*نشد|۴۰۴|404\b|page\s+not\s+found|"
    r"چنین صفحه‌ای وجود ندارد|nothing\s+found|no\s+results\s+found)",
    re.I,
)


@rule("noindex")
def check_noindex(ctx: SiteContext) -> Iterator[Issue]:
    noindexed = [p.url for p in ctx.html_pages if p.is_noindex]
    if not noindexed:
        return

    # Everything blocked is a red flag; a few blocked pages is normal hygiene.
    if len(noindexed) == len(ctx.html_pages):
        yield make_issue(
            "site-wide-noindex",
            "Entire site is set to noindex",
            "کل سایت روی noindex تنظیم شده",
            "همه‌ی صفحات بررسی‌شده دستور noindex دارند. این یعنی سایت به‌طور کامل از نتایج "
            "جستجو حذف می‌شود. تقریباً همیشه باقی‌مانده‌ی محیط توسعه یا تنظیم «منع موتورهای "
            "جستجو» در پیشخوان وردپرس است.",
            "در وردپرس: تنظیمات ← خواندن ← تیک «از موتورهای جستجو بخواهید این سایت را ایندکس نکنند» "
            "را بردار. در غیر این صورت تگ meta robots و هدر X-Robots-Tag را در سرور بررسی کن.",
            Severity.CRITICAL,
            Category.INDEXING,
            noindexed,
            sample(noindexed, 4),
            "https://developers.google.com/search/docs/crawling-indexing/block-indexing",
        )
        return

    yield make_issue(
        "noindex-pages",
        "Pages excluded with noindex",
        "صفحات خارج‌شده از ایندکس",
        f"{len(noindexed)} صفحه دستور noindex دارد. اگر عمدی است (صفحات ورود، سبد خرید، "
        "نتایج جستجوی داخلی) کاملاً درست است؛ فقط مطمئن شو صفحه‌ی مهمی اشتباهی داخل این فهرست نیفتاده.",
        "فهرست را مرور کن. نکته‌ی مهم: صفحه‌ای که noindex است را در robots.txt بلاک نکن — "
        "اگر گوگل نتواند صفحه را بخزد، تگ noindex را هم نمی‌بیند و ممکن است همچنان ایندکسش کند.",
        Severity.INFO,
        Category.INDEXING,
        noindexed,
        sample(noindexed, 5),
        "https://developers.google.com/search/docs/crawling-indexing/block-indexing",
    )


@rule("noindex-plus-robots-block")
def check_conflicting_directives(ctx: SiteContext) -> Iterator[Issue]:
    """noindex only works if Google is allowed to fetch the page and read it."""
    if not ctx.robots_txt:
        return

    disallowed_paths = []
    for raw in ctx.robots_txt.lower().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("disallow:"):
            value = line.split(":", 1)[1].strip()
            if value and value != "/":
                disallowed_paths.append(value)

    conflicts = []
    for page in ctx.html_pages:
        if not page.is_noindex:
            continue
        path = urlparse(page.url).path
        if any(path.startswith(d) for d in disallowed_paths):
            conflicts.append(page.url)

    if conflicts:
        yield make_issue(
            "noindex-blocked-by-robots",
            "noindex pages are also blocked in robots.txt",
            "صفحات noindex در robots.txt هم بلاک شده‌اند",
            f"{len(conflicts)} صفحه هم تگ noindex دارد و هم مسیرش در robots.txt بسته است. "
            "این ترکیب کار نمی‌کند: گوگل وقتی اجازه‌ی خزش ندارد، اصلاً تگ noindex را نمی‌بیند "
            "و ممکن است آدرس را بدون توضیحات ایندکس کند.",
            "برای خارج کردن صفحه از ایندکس، فقط noindex بگذار و مسیر را در robots.txt باز کن. "
            "robots.txt را برای صرفه‌جویی در بودجه‌ی خزش استفاده کن، نه برای حذف از ایندکس.",
            Severity.HIGH,
            Category.INDEXING,
            conflicts,
            sample(conflicts, 4),
            "https://developers.google.com/search/docs/crawling-indexing/block-indexing",
        )


@rule("soft-404")
def check_soft_404(ctx: SiteContext) -> Iterator[Issue]:
    suspects = []
    for page in ctx.html_pages:
        if page.status_code != 200 or page.word_count > 250:
            continue
        haystack = f"{page.title or ''} {' '.join(page.h1s)}"
        if SOFT_404_MARKERS.search(haystack):
            suspects.append(f"{page.url} («{(page.title or '')[:50]}»)")

    if suspects:
        yield make_issue(
            "soft-404",
            "Possible soft 404 pages",
            "احتمال وجود صفحات soft 404",
            f"{len(suspects)} صفحه با کد ۲۰۰ پاسخ می‌دهد ولی محتوایش می‌گوید چیزی پیدا نشد. "
            "گوگل به این حالت «soft 404» می‌گوید: بودجه‌ی خزش را می‌سوزاند و در گزارش پوشش "
            "سرچ کنسول به‌عنوان خطا ثبت می‌شود.",
            "صفحات ناموجود باید واقعاً کد ۴۰۴ (یا ۴۱۰ برای حذف دائمی) برگردانند. صفحه‌ی ۴۰۴ "
            "را طراحی‌شده و مفید بساز (جستجو، لینک به دسته‌های اصلی) ولی حتماً با کد ۴۰۴.",
            Severity.MEDIUM,
            Category.INDEXING,
            suspects,
            sample(suspects, 4),
            "https://developers.google.com/search/docs/crawling-indexing/http-network-errors",
        )


@rule("crawl-budget")
def check_crawl_waste(ctx: SiteContext) -> Iterator[Issue]:
    total = len(ctx.pages)
    if total < 10:
        return

    wasted = [
        p.url for p in ctx.pages
        if p.redirect_chain or (p.is_html and p.is_noindex) or (p.status_code >= 400)
    ]
    ratio = len(wasted) / total
    if ratio > 0.25:
        yield make_issue(
            "crawl-budget-waste",
            "High share of non-indexable crawled URLs",
            "سهم بالای آدرس‌های بی‌ارزش در خزش",
            f"{len(wasted)} آدرس از {total} آدرس خزیده‌شده ({ratio * 100:.0f}٪) قابل ایندکس نیست: "
            "یا ریدایرکت می‌شود، یا noindex است، یا خطا می‌دهد. گوگل برای هر سایت بودجه‌ی خزش "
            "محدودی دارد؛ صرف شدن آن روی آدرس‌های بی‌ارزش یعنی صفحات جدید و مهم دیرتر کشف می‌شوند.",
            "لینک‌های داخلی را مستقیماً به آدرس نهایی بده (نه به آدرسی که ریدایرکت می‌شود)، "
            "مسیرهای بی‌ارزش (فیلترها، جستجوی داخلی، صفحات چاپ) را در robots.txt ببند و "
            "لینک‌های خراب را اصلاح کن.",
            Severity.MEDIUM,
            Category.INDEXING,
            wasted,
            sample(wasted, 5),
            "https://developers.google.com/search/docs/crawling-indexing/large-site-managing-crawl-budget",
        )
