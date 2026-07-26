"""E-E-A-T signals: experience, expertise, authoritativeness, trust.

Google's quality raters look for who wrote a page, who stands behind the site,
and whether claims are backed. Since the helpful-content system merged into the
core algorithm in 2024 these signals affect ranking sitewide, not per page.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

DOCS_EEAT = "https://developers.google.com/search/docs/fundamentals/creating-helpful-content"

TRUST_PAGE_PATTERNS = {
    "about": (
        r"(درباره[\-_ ]?ما|درباره|about[\-_ ]?us|about|who[\-_ ]?we[\-_ ]?are|معرفی)",
        "صفحه‌ی «درباره ما»",
        "یک صفحه‌ی «درباره ما» بنویس که بگوید چه کسی پشت این سایت است، چه سابقه‌ای دارد، "
        "و چرا در این حوزه صلاحیت دارد. اسم واقعی، عکس واقعی و سابقه‌ی مشخص بنویس.",
    ),
    "contact": (
        r"(تماس|ارتباط[\-_ ]?با[\-_ ]?ما|contact|پشتیبانی|support)",
        "صفحه‌ی «تماس با ما»",
        "یک صفحه‌ی تماس با راه ارتباطی واقعی بگذار: آدرس فیزیکی، تلفن، ایمیل و فرم تماس. "
        "برای سایت‌های فروشگاهی و مالی این صفحه از نگاه گوگل الزامی است.",
    ),
    "privacy": (
        r"(حریم[\-_ ]?خصوصی|privacy|policy|قوانین|terms|شرایط)",
        "صفحه‌ی «حریم خصوصی» یا «قوانین»",
        "صفحه‌ی حریم خصوصی و شرایط استفاده اضافه کن و از فوتر به آن‌ها لینک بده.",
    ),
}

AUTHOR_MARKERS = re.compile(
    r"(rel=[\"']author|itemprop=[\"']author|class=[\"'][^\"']*author|\"author\"\s*:|"
    r"نویسنده|نوشته[‌ ]?ی|written\s+by|by\s+[A-Z][a-z]+)",
    re.I,
)


@rule("trust-pages")
def check_trust_pages(ctx: SiteContext) -> Iterator[Issue]:
    if not ctx.html_pages:
        return

    # Look at every URL and anchor text we saw across the crawl.
    haystack = " ".join(p.url for p in ctx.pages)
    for page in ctx.html_pages:
        haystack += " " + " ".join(f"{link.href} {link.anchor}" for link in page.links if link.is_internal)

    for key, (pattern, label, fix) in TRUST_PAGE_PATTERNS.items():
        if re.search(pattern, haystack, re.I):
            continue
        severity = Severity.MEDIUM if key in ("about", "contact") else Severity.LOW
        yield make_issue(
            f"missing-{key}-page",
            f"No {key} page found",
            f"{label} پیدا نشد",
            f"در خزش سایت هیچ لینکی به {label} پیدا نشد. گوگل در دستورالعمل ارزیابان کیفیت "
            "صراحتاً می‌گوید نبودِ اطلاعات درباره‌ی صاحب سایت و راه تماس، نشانه‌ی سایت "
            "کم‌اعتماد است — و برای سایت‌های حوزه‌ی پول و سلامت (YMYL) این مورد تعیین‌کننده است.",
            fix,
            severity,
            Category.EEAT,
            [ctx.start_url],
            "",
            DOCS_EEAT,
        )


@rule("authorship")
def check_authorship(ctx: SiteContext) -> Iterator[Issue]:
    content_pages = [p for p in ctx.indexable_pages if p.word_count >= 500]
    if len(content_pages) < 2:
        return

    without_author = [p.url for p in content_pages if not AUTHOR_MARKERS.search(p.html)]
    if len(without_author) >= max(2, len(content_pages) * 0.5):
        yield make_issue(
            "no-author-byline",
            "Content without an author byline",
            "محتوا بدون نام نویسنده",
            f"{len(without_author)} صفحه‌ی محتوایی از {len(content_pages)} صفحه هیچ نشانه‌ای از "
            "نویسنده ندارد. حرف E اول در E-E-A-T یعنی «تجربه»: گوگل می‌خواهد بداند چه کسی این "
            "را نوشته و چه تجربه‌ی دست‌اولی داشته. محتوای بی‌نام از نگاه گوگل به محتوای "
            "انبوه تولیدشده شبیه است.",
            "زیر عنوان هر مقاله نام نویسنده را با لینک به صفحه‌ی پروفایل او بگذار. صفحه‌ی "
            "پروفایل باید سابقه، تخصص و راه ارتباطی داشته باشد. همین را در داده ساختاریافته‌ی "
            "Article هم با فیلد author از نوع Person تکرار کن.",
            Severity.MEDIUM,
            Category.EEAT,
            without_author,
            sample(without_author, 4),
            DOCS_EEAT,
        )


@rule("citations")
def check_citations(ctx: SiteContext) -> Iterator[Issue]:
    long_pages = [p for p in ctx.indexable_pages if p.word_count >= 700]
    if len(long_pages) < 2:
        return

    uncited = [
        p.url for p in long_pages
        if len([link for link in p.links if not link.is_internal]) == 0
    ]
    if len(uncited) >= max(2, len(long_pages) * 0.6):
        yield make_issue(
            "no-external-citations",
            "Long content with no outbound citations",
            "محتوای طولانی بدون ارجاع به منبع",
            f"{len(uncited)} صفحه‌ی طولانی هیچ لینک خارجی ندارد. ارجاع به منابع معتبر یکی از "
            "روشن‌ترین سیگنال‌های اعتبار (Authoritativeness) است و برخلاف تصور رایج، لینک "
            "خروجی به منبع خوب به رتبه‌ی تو آسیب نمی‌زند.",
            "ادعاهای آماری و فنی را به منبع اصلی (مطالعه، مستندات رسمی، نهاد معتبر) لینک بده. "
            "دو تا چهار ارجاع معتبر در هر مقاله‌ی بلند کافی است.",
            Severity.LOW,
            Category.EEAT,
            uncited,
            sample(uncited, 3),
            DOCS_EEAT,
        )


@rule("social-proof")
def check_social_profiles(ctx: SiteContext) -> Iterator[Issue]:
    if not ctx.html_pages:
        return

    social_domains = (
        "instagram.com", "linkedin.com", "twitter.com", "x.com", "t.me", "telegram",
        "youtube.com", "facebook.com", "aparat.com", "github.com", "virgool.io",
    )
    found = False
    for page in ctx.html_pages:
        for link in page.links:
            if not link.is_internal and any(d in link.href.lower() for d in social_domains):
                found = True
                break
        if found:
            break

    if not found:
        yield make_issue(
            "no-social-profiles",
            "No links to social profiles",
            "لینکی به شبکه‌های اجتماعی وجود ندارد",
            "هیچ لینکی به پروفایل‌های شبکه‌های اجتماعی پیدا نشد. این پروفایل‌ها بخشی از هویت "
            "قابل تأیید برند هستند و گوگل از آن‌ها (به‌خصوص از طریق فیلد sameAs در نشانه‌گذاری "
            "Organization) برای اتصال سایت به موجودیت برند استفاده می‌کند.",
            "لینک شبکه‌های اجتماعی فعال را در فوتر بگذار و همان آدرس‌ها را در آرایه‌ی sameAs "
            "داخل نشانه‌گذاری Organization تکرار کن.",
            Severity.LOW,
            Category.EEAT,
            [ctx.start_url],
            "",
            DOCS_EEAT,
        )


@rule("security-txt")
def check_security_txt(ctx: SiteContext) -> Iterator[Issue]:
    if ctx.security_txt_found:
        return
    yield make_issue(
        "no-security-txt",
        "No security.txt",
        "فایل security.txt وجود ندارد",
        "فایل /.well-known/security.txt وجود ندارد. این یک استاندارد کوچک (RFC 9116) است که "
        "راه گزارش آسیب‌پذیری امنیتی را مشخص می‌کند؛ یکی از نشانه‌های بلوغ و اعتماد یک سایت است.",
        "یک فایل ساده در /.well-known/security.txt بگذار که شامل Contact و Expires باشد.",
        Severity.INFO,
        Category.EEAT,
        [ctx.start_url],
        "",
        "https://securitytxt.org/",
    )
