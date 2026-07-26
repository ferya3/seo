"""Internal linking depth, anchor text, orphan pages and outbound links."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator

from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

GENERIC_ANCHORS = {
    "اینجا", "کلیک کنید", "کلیک", "بیشتر", "ادامه مطلب", "ادامه", "لینک", "این لینک",
    "بیشتر بخوانید", "مشاهده", "جزئیات", "اطلاعات بیشتر", "دانلود",
    "click here", "here", "read more", "more", "link", "this", "learn more",
    "details", "download", "continue", "see more",
}

DOCS_LINKS = "https://developers.google.com/search/docs/crawling-indexing/links-crawlable"


@rule("orphan-pages")
def check_orphans(ctx: SiteContext) -> Iterator[Issue]:
    pages = ctx.indexable_pages
    if len(pages) < 3:
        return

    orphans = [p.url for p in pages if p.inlinks == 0 and p.url != ctx.start_url]
    weak = [f"{p.url} ({p.inlinks} لینک ورودی)" for p in pages if 0 < p.inlinks <= 1 and p.url != ctx.start_url]

    if orphans:
        yield make_issue(
            "orphan-pages",
            "Orphan pages (no internal links)",
            "صفحات یتیم (بدون لینک داخلی)",
            f"{len(orphans)} صفحه هیچ لینک داخلی از بقیه‌ی سایت دریافت نمی‌کند. این صفحات فقط "
            "از طریق سایت‌مپ کشف می‌شوند، هیچ اعتباری از صفحات دیگر نمی‌گیرند و گوگل آن‌ها را "
            "کم‌اهمیت تلقی می‌کند.",
            "از صفحات مرتبط و پربازدید به این صفحات لینک بده، با انکرتکست توصیفی که کلمه کلیدی "
            "صفحه‌ی مقصد در آن باشد. حداقل ۳ لینک داخلی برای هر صفحه‌ی مهم هدف بگیر.",
            Severity.HIGH,
            Category.LINKS,
            orphans,
            sample(orphans, 5),
            DOCS_LINKS,
        )

    if weak and len(weak) > len(pages) * 0.3:
        yield make_issue(
            "weak-internal-linking",
            "Weak internal linking",
            "لینک‌سازی داخلی ضعیف",
            f"{len(weak)} صفحه فقط یک لینک داخلی دارد. ساختار لینک داخلی مسطح و ضعیف باعث "
            "می‌شود اعتبار دامنه بین صفحات پخش نشود و صفحات عمیق‌تر هیچ‌وقت رتبه نگیرند.",
            "یک ساختار موضوعی (topic cluster) بساز: یک صفحه‌ی جامع مرکزی که به همه‌ی صفحات "
            "زیرموضوع لینک می‌دهد و همه‌ی آن‌ها هم به صفحه‌ی مرکزی برمی‌گردند.",
            Severity.MEDIUM,
            Category.LINKS,
            weak,
            sample(weak, 4),
            DOCS_LINKS,
        )


@rule("click-depth")
def check_depth(ctx: SiteContext) -> Iterator[Issue]:
    deep = [f"{p.url} (عمق {p.depth})" for p in ctx.indexable_pages if p.depth >= 4]
    if deep:
        yield make_issue(
            "deep-pages",
            "Pages buried too deep",
            "صفحات خیلی عمیق",
            f"{len(deep)} صفحه با ۴ کلیک یا بیشتر از صفحه‌ی اصلی قابل دسترسی است. هرچه عمق "
            "کلیک بیشتر باشد، گوگل صفحه را کم‌اهمیت‌تر می‌بیند و دیرتر می‌خزد.",
            "صفحات مهم را حداکثر در ۳ کلیک از صفحه‌ی اصلی قرار بده: از منو، از صفحات دسته‌بندی، "
            "یا از بخش «مطالب مرتبط» به آن‌ها لینک بده.",
            Severity.MEDIUM,
            Category.LINKS,
            deep,
            sample(deep, 4),
            DOCS_LINKS,
        )


@rule("anchor-text")
def check_anchors(ctx: SiteContext) -> Iterator[Issue]:
    generic: Counter[str] = Counter()
    empty_links = []
    total_internal = 0

    for page in ctx.html_pages:
        for link in page.links:
            if not link.is_internal:
                continue
            total_internal += 1
            anchor = link.anchor.strip().lower()
            if not anchor:
                empty_links.append(f"{page.url} → {link.href}")
            elif anchor in GENERIC_ANCHORS:
                generic[anchor] += 1

    generic_total = sum(generic.values())
    if generic_total and total_internal and generic_total / total_internal > 0.1:
        top = "، ".join(f"«{a}» ({n} بار)" for a, n in generic.most_common(4))
        yield make_issue(
            "generic-anchor-text",
            "Generic anchor text",
            "انکرتکست عمومی و بی‌معنا",
            f"{generic_total} لینک داخلی از {total_internal} لینک ({generic_total / total_internal * 100:.0f}٪) "
            f"انکرتکست عمومی دارند: {top}. انکرتکست یکی از قوی‌ترین سیگنال‌ها برای فهمیدن "
            "موضوع صفحه‌ی مقصد است و «اینجا کلیک کنید» هیچ اطلاعاتی به گوگل نمی‌دهد.",
            "انکرتکست را توصیفی بنویس: به‌جای «برای اطلاعات بیشتر اینجا کلیک کنید» بنویس "
            "«راهنمای کامل انتخاب کلمه کلیدی». عبارت باید موضوع صفحه‌ی مقصد را بگوید.",
            Severity.MEDIUM,
            Category.LINKS,
            [],
            top,
            DOCS_LINKS,
        )

    if empty_links:
        yield make_issue(
            "empty-anchor-text",
            "Links with no anchor text",
            "لینک‌های بدون متن",
            f"{len(empty_links)} لینک داخلی هیچ متنی ندارد (معمولاً لینک روی تصویر یا آیکون). "
            "گوگل در این حالت از alt تصویر استفاده می‌کند؛ اگر alt هم نباشد لینک هیچ سیگنال معنایی نمی‌دهد.",
            "به لینک‌های تصویری alt توصیفی بده، یا برای لینک‌های آیکونی از aria-label استفاده کن.",
            Severity.LOW,
            Category.LINKS,
            empty_links,
            sample(empty_links, 3),
            DOCS_LINKS,
        )


@rule("javascript-links")
def check_js_links(ctx: SiteContext) -> Iterator[Issue]:
    offenders = []
    pattern = re.compile(r"<(?:a|span|div|button)[^>]*\bonclick\s*=\s*[\"'][^\"']*(?:location|href)", re.I)
    for page in ctx.html_pages:
        hits = pattern.findall(page.html)
        if hits:
            offenders.append(f"{page.url} ({len(hits)} مورد)")

    if offenders:
        yield make_issue(
            "js-navigation",
            "Navigation via JavaScript onclick",
            "پیمایش با onclick به‌جای لینک واقعی",
            f"{len(offenders)} صفحه برای رفتن به صفحه‌ی دیگر از onclick استفاده می‌کند نه از "
            "تگ <a href>. گوگل فقط لینک‌هایی را دنبال می‌کند که تگ a با ویژگی href داشته باشند؛ "
            "بقیه اصلاً کشف نمی‌شوند.",
            "همه‌ی مسیرهای پیمایش را با `<a href=\"/آدرس-واقعی\">` بنویس. اگر رفتار جاوااسکریپتی "
            "لازم است، آن را روی همان تگ a اضافه کن ولی href معتبر را نگه دار.",
            Severity.HIGH,
            Category.LINKS,
            offenders,
            sample(offenders, 3),
            DOCS_LINKS,
        )


@rule("external-links")
def check_external(ctx: SiteContext) -> Iterator[Issue]:
    if ctx.broken_external:
        items = [f"{url} ({status or error}) — در {source}" for url, source, status, error in ctx.broken_external]
        yield make_issue(
            "broken-external-links",
            "Broken outbound links",
            "لینک‌های خروجی خراب",
            f"{len(items)} لینک خارجی از {ctx.external_checked} لینک بررسی‌شده کار نمی‌کند. "
            "لینک خراب سیگنال محتوای رهاشده و به‌روزنشده است — دقیقاً چیزی که ارزیابی E-E-A-T "
            "به آن حساس است.",
            "لینک‌ها را به‌روز کن یا حذفشان کن. اگر منبع اصلی از بین رفته، به نسخه‌ی آرشیو "
            "(web.archive.org) لینک بده یا منبع معتبر جایگزین پیدا کن.",
            Severity.MEDIUM,
            Category.LINKS,
            items,
            sample(items, 4),
            "",
        )

    # Outbound links opening in a new tab should carry rel="noopener" for security.
    unsafe = []
    for page in ctx.html_pages:
        for link in page.links:
            if link.is_internal:
                continue
            if 'target="_blank"' in page.html and "noopener" not in link.rel.lower():
                unsafe.append(f"{page.url} → {link.href}")
                break

    if len(unsafe) > 2:
        yield make_issue(
            "unsafe-target-blank",
            "target=\"_blank\" without rel=noopener",
            "لینک خروجی بدون rel=noopener",
            f"{len(unsafe)} صفحه لینک خارجی دارد که در تب جدید باز می‌شود ولی rel=\"noopener\" ندارد. "
            "صفحه‌ی مقصد می‌تواند از طریق window.opener به صفحه‌ی تو دسترسی پیدا کند.",
            'به این لینک‌ها `rel="noopener noreferrer"` اضافه کن.',
            Severity.LOW,
            Category.LINKS,
            unsafe,
            sample(unsafe, 3),
            "",
        )
