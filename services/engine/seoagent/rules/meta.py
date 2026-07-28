"""Title tags, meta descriptions, canonicals and social cards."""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from ..fetcher import normalize_url, same_site
from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

# The engine runs standalone from its own venv as well as inside the platform,
# so the repo root is put on the path the same way `ai.py` and `netguard.py`
# do it.
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# One definition of "a good title length", shared with the optimizer and the
# competitor service. Re-exported here because the rules read them by name.
from shared.seo import DESC_MAX, DESC_MIN, TITLE_MAX, TITLE_MIN  # noqa: E402

DOCS_TITLE = "https://developers.google.com/search/docs/appearance/title-link"
DOCS_SNIPPET = "https://developers.google.com/search/docs/appearance/snippet"
DOCS_CANONICAL = "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls"


@rule("title")
def check_titles(ctx: SiteContext) -> Iterator[Issue]:
    missing, short, long_, duplicated = [], [], [], defaultdict(list)

    for page in ctx.indexable_pages:
        title = (page.title or "").strip()
        if not title:
            missing.append(page.url)
            continue
        duplicated[title.lower()].append(page.url)
        if len(title) < TITLE_MIN:
            short.append(f"{page.url} ({len(title)} کاراکتر: «{title}»)")
        elif len(title) > TITLE_MAX:
            long_.append(f"{page.url} ({len(title)} کاراکتر)")

    if missing:
        yield make_issue(
            "title-missing",
            "Missing <title> tag",
            "تگ عنوان وجود ندارد",
            f"{len(missing)} صفحه‌ی قابل ایندکس هیچ تگ <title> ندارد. عنوان مهم‌ترین "
            "سیگنال روی صفحه برای فهمیدن موضوع صفحه است و همان چیزی است که در نتایج "
            "جستجو به‌عنوان تیتر آبی نمایش داده می‌شود.",
            "برای هر صفحه یک عنوان یکتا بین ۲۵ تا ۶۰ کاراکتر بنویس که کلمه کلیدی اصلی "
            "در ابتدای آن باشد و نام برند در انتها بیاید. مثال: «آموزش سئو تکنیکال | نام‌برند».",
            Severity.CRITICAL,
            Category.CONTENT,
            missing,
            sample(missing),
            DOCS_TITLE,
        )

    if short:
        yield make_issue(
            "title-too-short",
            "Title too short",
            "عنوان بیش از حد کوتاه است",
            f"{len(short)} صفحه عنوانی کوتاه‌تر از {TITLE_MIN} کاراکتر دارد. عنوان کوتاه "
            "فضای ارزشمند نتایج جستجو را هدر می‌دهد و کلمات کلیدی مرتبط را از دست می‌دهد.",
            "عنوان را طوری بازنویسی کن که هم کلمه کلیدی اصلی و هم یک مزیت یا مشخصه‌ی "
            "متمایزکننده (سال، «راهنمای کامل»، «رایگان»، قیمت و ...) در آن بیاید.",
            Severity.MEDIUM,
            Category.CONTENT,
            short,
            sample(short, 3),
            DOCS_TITLE,
        )

    if long_:
        yield make_issue(
            "title-too-long",
            "Title too long",
            "عنوان بیش از حد بلند است",
            f"{len(long_)} صفحه عنوانی بلندتر از {TITLE_MAX} کاراکتر دارد. گوگل عنوان را "
            "بر اساس عرض پیکسلی می‌برد و انتهای آن با «...» حذف می‌شود؛ در این حالت "
            "پیام اصلی تو به کاربر نمی‌رسد.",
            "مهم‌ترین کلمات را به ابتدای عنوان منتقل کن و بخش‌های تکراری (مثل نام برند در "
            "همه‌ی صفحات) را کوتاه کن تا زیر ۶۰ کاراکتر بماند.",
            Severity.LOW,
            Category.CONTENT,
            long_,
            sample(long_, 3),
            DOCS_TITLE,
        )

    dupes = {title: urls for title, urls in duplicated.items() if len(urls) > 1}
    if dupes:
        flat = [url for urls in dupes.values() for url in urls]
        first = next(iter(dupes))
        yield make_issue(
            "title-duplicate",
            "Duplicate titles",
            "عنوان‌های تکراری",
            f"{len(dupes)} عنوان روی مجموعاً {len(flat)} صفحه تکرار شده است. وقتی چند صفحه "
            "عنوان یکسان دارند گوگل آن‌ها را رقیب هم می‌بیند و ممکن است فقط یکی را ایندکس کند.",
            "برای هر صفحه عنوان یکتا بنویس. اگر صفحات واقعاً محتوای یکسانی دارند (مثل صفحات "
            "صفحه‌بندی یا فیلترشده)، تگ canonical آن‌ها را به نسخه‌ی اصلی اشاره بده.",
            Severity.HIGH,
            Category.CONTENT,
            flat,
            f"مثال: «{first}» روی {len(dupes[first])} صفحه",
            DOCS_TITLE,
        )


@rule("meta-description")
def check_descriptions(ctx: SiteContext) -> Iterator[Issue]:
    missing, too_short, too_long, duplicated = [], [], [], defaultdict(list)

    for page in ctx.indexable_pages:
        desc = (page.meta_description or "").strip()
        if not desc:
            missing.append(page.url)
            continue
        duplicated[desc.lower()].append(page.url)
        if len(desc) < DESC_MIN:
            too_short.append(f"{page.url} ({len(desc)} کاراکتر)")
        elif len(desc) > DESC_MAX:
            too_long.append(f"{page.url} ({len(desc)} کاراکتر)")

    if missing:
        yield make_issue(
            "meta-description-missing",
            "Missing meta description",
            "متا دیسکریپشن وجود ندارد",
            f"{len(missing)} صفحه متا دیسکریپشن ندارد. متا دیسکریپشن فاکتور رتبه‌بندی "
            "مستقیم نیست، اما اسنیپت زیر عنوان را می‌سازد و مستقیماً روی نرخ کلیک (CTR) اثر می‌گذارد. "
            "بدون آن گوگل خودش تکه‌ای از متن صفحه را برمی‌دارد که معمولاً جذاب نیست.",
            "برای هر صفحه یک توضیح ۷۰ تا ۱۶۰ کاراکتری بنویس که به سؤال کاربر جواب بدهد و "
            "یک فراخوان به اقدام داشته باشد. کلمه کلیدی را طبیعی در آن بیاور (در نتایج پررنگ می‌شود).",
            Severity.MEDIUM,
            Category.CONTENT,
            missing,
            sample(missing),
            DOCS_SNIPPET,
        )

    if too_short:
        yield make_issue(
            "meta-description-short",
            "Meta description too short",
            "متا دیسکریپشن کوتاه است",
            f"{len(too_short)} صفحه توضیحی کوتاه‌تر از {DESC_MIN} کاراکتر دارد و از فضای اسنیپت کامل استفاده نمی‌کند.",
            "توضیح را تا حدود ۱۵۰ کاراکتر گسترش بده: مسئله‌ی کاربر، راه‌حل تو، و یک دلیل برای کلیک.",
            Severity.LOW,
            Category.CONTENT,
            too_short,
            sample(too_short, 3),
            DOCS_SNIPPET,
        )

    if too_long:
        yield make_issue(
            "meta-description-long",
            "Meta description too long",
            "متا دیسکریپشن بلند است",
            f"{len(too_long)} صفحه توضیحی بلندتر از {DESC_MAX} کاراکتر دارد که در نتایج بریده می‌شود.",
            "پیام اصلی و فراخوان به اقدام را به ۱۲۰ کاراکتر اول منتقل کن.",
            Severity.LOW,
            Category.CONTENT,
            too_long,
            sample(too_long, 3),
            DOCS_SNIPPET,
        )

    dupes = {d: urls for d, urls in duplicated.items() if len(urls) > 1}
    if dupes:
        flat = [url for urls in dupes.values() for url in urls]
        yield make_issue(
            "meta-description-duplicate",
            "Duplicate meta descriptions",
            "متا دیسکریپشن تکراری",
            f"{len(dupes)} توضیح روی {len(flat)} صفحه تکرار شده است. این معمولاً نشانه‌ی "
            "قالب پیش‌فرض CMS است و یعنی هیچ‌کدام از صفحات پیام اختصاصی خودشان را ندارند.",
            "توضیح هر صفحه را جداگانه بنویس، یا اگر تعداد صفحات زیاد است از یک الگوی پویا "
            "استفاده کن که فیلدهای واقعی همان صفحه (عنوان، دسته، قیمت) را داخل خودش بیاورد.",
            Severity.LOW,
            Category.CONTENT,
            flat,
            sample(flat, 4),
            DOCS_SNIPPET,
        )


@rule("canonical")
def check_canonicals(ctx: SiteContext) -> Iterator[Issue]:
    missing, external, mismatched, non_indexable_target = [], [], [], []
    noindex_urls = {normalize_url(p.url) for p in ctx.html_pages if p.is_noindex}

    for page in ctx.indexable_pages:
        if not page.canonical:
            missing.append(page.url)
            continue
        canonical = normalize_url(page.canonical)
        current = normalize_url(page.final_url or page.url)
        if not same_site(canonical, ctx.origin, follow_subdomains=True):
            external.append(f"{page.url} → {page.canonical}")
        elif canonical != current:
            mismatched.append(f"{page.url} → {page.canonical}")
            if canonical in noindex_urls:
                non_indexable_target.append(f"{page.url} → {page.canonical}")

    if missing:
        yield make_issue(
            "canonical-missing",
            "Missing canonical tag",
            "تگ canonical تعریف نشده",
            f"{len(missing)} صفحه تگ rel=canonical ندارد. بدون آن، اگر همان محتوا از "
            "چند آدرس در دسترس باشد (با /، بدون /، با پارامتر UTM، http و https) گوگل "
            "خودش باید حدس بزند کدام نسخه اصلی است و ممکن است اشتباه انتخاب کند.",
            "در <head> هر صفحه یک <link rel=\"canonical\" href=\"آدرس-مطلق-همان-صفحه\"> بگذار. "
            "حتماً آدرس مطلق و با پروتکل کامل باشد، نه نسبی.",
            Severity.MEDIUM,
            Category.INDEXING,
            missing,
            sample(missing),
            DOCS_CANONICAL,
        )

    if external:
        yield make_issue(
            "canonical-external",
            "Canonical points to another domain",
            "canonical به دامنه‌ی دیگری اشاره می‌کند",
            f"{len(external)} صفحه canonical خود را به دامنه‌ی دیگری داده است. این یعنی به "
            "گوگل می‌گویی «این صفحه را ایندکس نکن، نسخه‌ی اصلی جای دیگری است» — که اگر عمدی "
            "نباشد کل صفحه را از نتایج حذف می‌کند.",
            "اگر محتوا مال خودت است canonical را به آدرس همین دامنه اصلاح کن. اگر محتوای "
            "بازنشرشده است، این تنظیم درست است و نیازی به تغییر ندارد.",
            Severity.CRITICAL,
            Category.INDEXING,
            external,
            sample(external, 3),
            DOCS_CANONICAL,
        )

    if non_indexable_target:
        yield make_issue(
            "canonical-to-noindex",
            "Canonical points to a noindex page",
            "canonical به صفحه‌ی noindex اشاره دارد",
            f"{len(non_indexable_target)} صفحه canonical خود را به صفحه‌ای داده که خودش noindex است. "
            "این سیگنال متناقض است: گوگل نمی‌داند کدام نسخه را نگه دارد و معمولاً هر دو را کنار می‌گذارد.",
            "یا noindex را از صفحه‌ی مقصد بردار، یا canonical را به یک صفحه‌ی قابل ایندکس تغییر بده.",
            Severity.HIGH,
            Category.INDEXING,
            non_indexable_target,
            sample(non_indexable_target, 3),
            DOCS_CANONICAL,
        )
    elif mismatched:
        yield make_issue(
            "canonical-mismatch",
            "Canonical differs from page URL",
            "canonical با آدرس خود صفحه فرق دارد",
            f"{len(mismatched)} صفحه canonical خود را به آدرس دیگری داده‌اند. اگر این صفحات "
            "واقعاً نسخه‌ی تکراری هستند مشکلی نیست، اما اگر محتوای یکتا دارند از ایندکس خارج می‌شوند.",
            "فهرست را مرور کن: هر صفحه‌ای که محتوای مستقل دارد باید canonical به خودش بدهد.",
            Severity.MEDIUM,
            Category.INDEXING,
            mismatched,
            sample(mismatched, 3),
            DOCS_CANONICAL,
        )


@rule("social-cards")
def check_social(ctx: SiteContext) -> Iterator[Issue]:
    missing_og, missing_image = [], []
    for page in ctx.indexable_pages:
        if not page.og.get("og:title") and not page.og.get("og:description"):
            missing_og.append(page.url)
        elif not page.og.get("og:image"):
            missing_image.append(page.url)

    if missing_og:
        yield make_issue(
            "og-missing",
            "Missing Open Graph tags",
            "تگ‌های Open Graph وجود ندارد",
            f"{len(missing_og)} صفحه تگ‌های og: ندارد. وقتی لینک صفحه در تلگرام، واتساپ، "
            "لینکدین یا ایکس به اشتراک گذاشته می‌شود، پیش‌نمایش بدون عنوان و تصویر نمایش داده "
            "می‌شود و نرخ کلیک به‌شدت افت می‌کند. اشتراک‌گذاری اجتماعی به‌طور غیرمستقیم روی "
            "لینک‌سازی و برندسازی اثر دارد.",
            "به <head> اضافه کن: og:title، og:description، og:image (۱۲۰۰×۶۳۰ پیکسل)، "
            "og:url و og:type. برای ایکس هم twitter:card=summary_large_image بگذار.",
            Severity.LOW,
            Category.CONTENT,
            missing_og,
            sample(missing_og),
            "https://ogp.me/",
        )

    if missing_image:
        yield make_issue(
            "og-image-missing",
            "Open Graph image missing",
            "تصویر Open Graph تعریف نشده",
            f"{len(missing_image)} صفحه تگ og:image ندارد و هنگام اشتراک‌گذاری بدون تصویر دیده می‌شود.",
            "یک تصویر ۱۲۰۰×۶۳۰ پیکسل با آدرس مطلق در og:image قرار بده.",
            Severity.LOW,
            Category.CONTENT,
            missing_image,
            sample(missing_image, 3),
            "https://ogp.me/",
        )
