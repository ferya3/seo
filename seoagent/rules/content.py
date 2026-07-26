"""Headings, content depth, duplication and on-page keyword usage."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterator

from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

THIN_CONTENT_WORDS = 300
SHALLOW_CONTENT_WORDS = 600

DOCS_HELPFUL = "https://developers.google.com/search/docs/fundamentals/creating-helpful-content"
DOCS_HEADINGS = "https://developers.google.com/search/docs/appearance/structured-data"


@rule("headings")
def check_headings(ctx: SiteContext) -> Iterator[Issue]:
    missing_h1, multiple_h1, empty_h1, broken_order, no_subheads = [], [], [], [], []

    for page in ctx.indexable_pages:
        h1s = page.h1s
        if not h1s:
            missing_h1.append(page.url)
        elif len(h1s) > 1:
            multiple_h1.append(f"{page.url} ({len(h1s)} تا H1)")
        elif len(h1s[0].strip()) < 3:
            empty_h1.append(page.url)

        levels = [lvl for lvl, _ in page.headings]
        for previous, current in zip(levels, levels[1:], strict=False):
            if current - previous > 1:
                broken_order.append(f"{page.url} (H{previous} → H{current})")
                break

        if page.word_count > SHALLOW_CONTENT_WORDS and not any(lvl == 2 for lvl in levels):
            no_subheads.append(f"{page.url} ({page.word_count} کلمه، بدون H2)")

    if missing_h1:
        yield make_issue(
            "h1-missing",
            "Missing H1 heading",
            "تیتر H1 وجود ندارد",
            f"{len(missing_h1)} صفحه هیچ تگ H1 ندارد. H1 به گوگل و به کاربر می‌گوید موضوع "
            "اصلی صفحه چیست و پایه‌ی ساختار معنایی صفحه است.",
            "در هر صفحه دقیقاً یک H1 بگذار که موضوع اصلی و کلمه کلیدی هدف را داشته باشد. "
            "H1 می‌تواند کمی با <title> فرق کند تا دو کلمه کلیدی مرتبط را پوشش بدهی.",
            Severity.HIGH,
            Category.CONTENT,
            missing_h1,
            sample(missing_h1),
            DOCS_HEADINGS,
        )

    if multiple_h1:
        yield make_issue(
            "h1-multiple",
            "Multiple H1 headings",
            "چند تیتر H1 در یک صفحه",
            f"{len(multiple_h1)} صفحه بیش از یک H1 دارد. گوگل با چند H1 مشکل فنی ندارد، "
            "اما در عمل موضوع اصلی صفحه را مبهم می‌کند و معمولاً نشانه‌ی این است که "
            "قالب سایت از تگ‌های تیتر برای استایل‌دهی استفاده می‌کند نه برای ساختار.",
            "یک H1 برای موضوع اصلی نگه دار و بقیه را به H2 تبدیل کن.",
            Severity.LOW,
            Category.CONTENT,
            multiple_h1,
            sample(multiple_h1, 3),
            DOCS_HEADINGS,
        )

    if empty_h1:
        yield make_issue(
            "h1-empty",
            "Empty H1",
            "تیتر H1 خالی است",
            f"{len(empty_h1)} صفحه H1 خالی یا تقریباً خالی دارد (معمولاً H1 فقط شامل لوگو).",
            "متن واقعی داخل H1 بگذار. اگر لوگو داخل H1 است، حداقل متن جایگزین (alt) معنادار بده "
            "و برای عنوان محتوا یک H1 متنی جدا در نظر بگیر.",
            Severity.MEDIUM,
            Category.CONTENT,
            empty_h1,
            sample(empty_h1, 3),
            DOCS_HEADINGS,
        )

    if broken_order:
        yield make_issue(
            "heading-order",
            "Skipped heading levels",
            "ترتیب تیترها پرش دارد",
            f"در {len(broken_order)} صفحه سطح تیترها پرش دارد (مثلاً بعد از H2 مستقیم H4 آمده). "
            "این ساختار معنایی صفحه را برای گوگل و برای صفحه‌خوان‌ها مبهم می‌کند.",
            "تیترها را پشت سر هم و بدون پرش استفاده کن: H1 → H2 → H3. برای بزرگ/کوچک کردن "
            "اندازه‌ی متن از CSS استفاده کن، نه از تغییر سطح تیتر.",
            Severity.LOW,
            Category.CONTENT,
            broken_order,
            sample(broken_order, 3),
            DOCS_HEADINGS,
        )

    if no_subheads:
        yield make_issue(
            "no-subheadings",
            "Long content without subheadings",
            "محتوای طولانی بدون زیرتیتر",
            f"{len(no_subheads)} صفحه‌ی طولانی هیچ H2 ندارد. گوگل برای انتخاب پاسخ در "
            "AI Overviews و اسنیپت‌های ویژه، محتوا را به «پاساژ» تقسیم می‌کند و زیرتیترها "
            "مرز این پاساژها هستند. بدون آن‌ها شانس دیده‌شدن در این بخش‌ها کم می‌شود.",
            "متن را با H2 به بخش‌های موضوعی بشکن و هر زیرتیتر را به شکل یک سؤال یا یک "
            "عبارت کلیدی مرتبط بنویس.",
            Severity.MEDIUM,
            Category.AI_SEARCH,
            no_subheads,
            sample(no_subheads, 3),
            DOCS_HELPFUL,
        )


@rule("content-depth")
def check_content_depth(ctx: SiteContext) -> Iterator[Issue]:
    thin, shallow = [], []
    for page in ctx.indexable_pages:
        if page.word_count < THIN_CONTENT_WORDS:
            thin.append(f"{page.url} ({page.word_count} کلمه)")
        elif page.word_count < SHALLOW_CONTENT_WORDS:
            shallow.append(f"{page.url} ({page.word_count} کلمه)")

    if thin:
        yield make_issue(
            "thin-content",
            "Thin content",
            "محتوای کم‌عمق",
            f"{len(thin)} صفحه کمتر از {THIN_CONTENT_WORDS} کلمه محتوای اصلی دارد. سیستم "
            "«محتوای مفید» گوگل که از سال ۲۰۲۴ داخل الگوریتم اصلی ادغام شده، صفحاتی را که "
            "ارزش افزوده‌ی کافی ندارند پایین می‌آورد — و صفحات کم‌ارزش زیاد، رتبه‌ی کل دامنه را هم پایین می‌کشد.",
            "برای هر صفحه تصمیم بگیر: یا محتوا را عمیق کن (تجربه‌ی دست‌اول، داده، مثال، تصویر "
            "اختصاصی، پرسش‌های واقعی کاربر)، یا صفحه را با ۳۰۱ به صفحه‌ی جامع‌تر ادغام کن، "
            "یا اگر صرفاً کاربردی است (مثل صفحه تماس) با noindex از ایندکس خارجش کن.",
            Severity.HIGH,
            Category.CONTENT,
            thin,
            sample(thin, 5),
            DOCS_HELPFUL,
        )

    if shallow:
        yield make_issue(
            "shallow-content",
            "Content could be more comprehensive",
            "محتوا می‌تواند جامع‌تر باشد",
            f"{len(shallow)} صفحه بین {THIN_CONTENT_WORDS} تا {SHALLOW_CONTENT_WORDS} کلمه دارد. "
            "این حجم برای موضوعات ساده کافی است، اما برای کلمات کلیدی رقابتی معمولاً کم است.",
            "نتایج صفحه اول گوگل برای کلمه کلیدی هدف را ببین و زیرموضوع‌هایی که آن‌ها پوشش "
            "داده‌اند و تو نداده‌ای را اضافه کن.",
            Severity.LOW,
            Category.CONTENT,
            shallow,
            sample(shallow, 3),
            DOCS_HELPFUL,
        )


@rule("duplicate-content")
def check_duplicate_content(ctx: SiteContext) -> Iterator[Issue]:
    groups: dict[str, list[str]] = defaultdict(list)
    for page in ctx.indexable_pages:
        if page.word_count >= 100 and page.content_hash:
            groups[page.content_hash].append(page.url)

    duplicates = {h: urls for h, urls in groups.items() if len(urls) > 1}
    if not duplicates:
        return

    flat = [url for urls in duplicates.values() for url in urls]
    example = next(iter(duplicates.values()))
    yield make_issue(
        "duplicate-content",
        "Duplicate page content",
        "محتوای کاملاً تکراری",
        f"{len(duplicates)} گروه از صفحات محتوای متنی یکسان دارند (مجموعاً {len(flat)} صفحه). "
        "گوگل فقط یکی از آن‌ها را ایندکس می‌کند و بقیه بودجه‌ی خزش را می‌سوزانند.",
        "نسخه‌ی اصلی را انتخاب کن و بقیه را با rel=canonical به آن اشاره بده، یا اگر آدرس "
        "دیگری لازم نیست با ریدایرکت ۳۰۱ به نسخه‌ی اصلی منتقلشان کن.",
        Severity.HIGH,
        Category.INDEXING,
        flat,
        f"مثال: {sample(example, 3)}",
        "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
    )


@rule("keyword-usage")
def check_keyword_usage(ctx: SiteContext) -> Iterator[Issue]:
    """Only runs when the user supplied target keywords for the audit."""
    if not ctx.target_keywords:
        return

    pages = ctx.indexable_pages
    if not pages:
        return

    for keyword in ctx.target_keywords[:10]:
        kw = keyword.strip().lower()
        if not kw:
            continue
        in_title = [p.url for p in pages if kw in (p.title or "").lower()]
        in_h1 = [p.url for p in pages if any(kw in h.lower() for h in p.h1s)]
        in_body = [p.url for p in pages if kw in p.text.lower()]

        if not in_body:
            yield make_issue(
                f"keyword-absent-{abs(hash(kw)) % 10**6}",
                f"Target keyword not found: {keyword}",
                f"کلمه کلیدی هدف در سایت پیدا نشد: «{keyword}»",
                f"هیچ‌کدام از {len(pages)} صفحه‌ی بررسی‌شده عبارت «{keyword}» را در متن اصلی ندارند. "
                "بدون وجود عبارت هدف (یا مترادف‌های نزدیک آن) در محتوا، رتبه گرفتن برای آن عملاً ممکن نیست.",
                f"یک صفحه‌ی اختصاصی برای «{keyword}» بساز که این عبارت در عنوان، H1، پاراگراف "
                "اول و آدرس صفحه بیاید، و از صفحات مرتبط دیگر با همین انکرتکست به آن لینک بده.",
                Severity.HIGH,
                Category.CONTENT,
                [ctx.start_url],
                "",
                DOCS_HELPFUL,
            )
            continue

        if not in_title and not in_h1:
            yield make_issue(
                f"keyword-weak-{abs(hash(kw)) % 10**6}",
                f"Target keyword not in title or H1: {keyword}",
                f"«{keyword}» در هیچ عنوان یا H1 نیست",
                f"عبارت «{keyword}» در متن {len(in_body)} صفحه آمده، ولی در هیچ <title> یا H1 نیست. "
                "این یعنی هیچ صفحه‌ای به‌روشنی خودش را به‌عنوان پاسخ این جستجو معرفی نکرده است.",
                f"صفحه‌ای که بیشترین ارتباط را با «{keyword}» دارد به‌عنوان صفحه‌ی هدف انتخاب کن و "
                "این عبارت را در ابتدای <title> و داخل H1 آن بیاور.",
                Severity.MEDIUM,
                Category.CONTENT,
                in_body[:20],
                sample(in_body, 3),
                DOCS_HELPFUL,
            )

        if len(in_title) > 1:
            yield make_issue(
                f"keyword-cannibal-{abs(hash(kw)) % 10**6}",
                f"Keyword cannibalisation: {keyword}",
                f"هم‌نوع‌خواری کلمه کلیدی: «{keyword}»",
                f"{len(in_title)} صفحه عبارت «{keyword}» را در <title> خود دارند. این صفحات با هم "
                "رقابت می‌کنند، قدرت لینک‌ها بین‌شان تقسیم می‌شود و گوگل مدام بین آن‌ها جابه‌جا می‌شود.",
                "یک صفحه را به‌عنوان صفحه‌ی اصلی این کلمه کلیدی انتخاب کن، بقیه را روی عبارت‌های "
                "بلندتر و دقیق‌تر بازتعریف کن و از آن‌ها به صفحه‌ی اصلی لینک داخلی بده.",
                Severity.MEDIUM,
                Category.CONTENT,
                in_title,
                sample(in_title, 4),
                DOCS_HELPFUL,
            )


@rule("keyword-stuffing")
def check_keyword_stuffing(ctx: SiteContext) -> Iterator[Issue]:
    """Flag pages where one term dominates the copy — a classic spam signal."""
    offenders = []
    stopish = re.compile(r"^[\W\d_]+$")

    for page in ctx.indexable_pages:
        if page.word_count < 150:
            continue
        # 3+ characters, not 4+: plenty of meaningful Persian words are three
        # letters ("سئو", "وام", "بیمه") and those are exactly the ones people stuff.
        # Filtering short tokens also removes most function words, so the
        # remaining pool is smaller in Persian than the raw word count suggests.
        words = re.findall(r"[\w؀-ۿ']{3,}", page.text.lower())
        if len(words) < 80:
            continue
        counts = Counter(words)
        term, hits = counts.most_common(1)[0]
        if stopish.match(term):
            continue
        density = hits / len(words)
        if density > 0.06 and hits >= 15:
            offenders.append(f"{page.url} («{term}» {density * 100:.1f}٪ از متن)")

    if offenders:
        yield make_issue(
            "keyword-stuffing",
            "Possible keyword stuffing",
            "احتمال انباشت کلمه کلیدی",
            f"در {len(offenders)} صفحه یک عبارت بیش از ۶٪ کل کلمات را تشکیل می‌دهد. "
            "تکرار غیرطبیعی کلمه کلیدی در سیاست‌های اسپم گوگل صراحتاً ذکر شده و می‌تواند "
            "منجر به اقدام دستی (manual action) شود.",
            "متن را طبیعی بازنویسی کن و به‌جای تکرار عین عبارت، از مترادف‌ها، اصطلاحات مرتبط "
            "و موجودیت‌های هم‌خانواده استفاده کن. گوگل سال‌هاست معنا را می‌فهمد و نیازی به تکرار عینی نیست.",
            Severity.HIGH,
            Category.CONTENT,
            offenders,
            sample(offenders, 3),
            "https://developers.google.com/search/docs/essentials/spam-policies",
        )


@rule("content-freshness")
def check_freshness(ctx: SiteContext) -> Iterator[Issue]:
    """Dates matter for query-deserves-freshness topics and for E-E-A-T."""
    undated = []
    for page in ctx.indexable_pages:
        if page.word_count < 400:
            continue
        has_time_tag = "<time" in page.html.lower()
        has_date_meta = any(
            key in page.html.lower()
            for key in ("article:published_time", "datepublished", "datemodified")
        )
        if not has_time_tag and not has_date_meta:
            undated.append(page.url)

    if undated:
        yield make_issue(
            "no-publish-date",
            "Articles without a visible date",
            "مقالات بدون تاریخ انتشار",
            f"{len(undated)} صفحه‌ی محتوایی هیچ تاریخ انتشار یا به‌روزرسانی ماشین‌خوانی ندارد. "
            "گوگل برای موضوعاتی که تازگی اهمیت دارد (اخبار، قیمت، نسخه‌ی نرم‌افزار، قوانین) "
            "به تاریخ نگاه می‌کند، و نبودِ تاریخ یکی از سیگنال‌های ضعف اعتماد است.",
            "تاریخ انتشار را هم به‌صورت متنی برای کاربر نمایش بده و هم در داده ساختاریافته‌ی "
            "Article با فیلدهای datePublished و dateModified قرار بده. تاریخ را فقط وقتی "
            "به‌روز کن که واقعاً محتوا را تغییر داده‌ای.",
            Severity.MEDIUM,
            Category.EEAT,
            undated,
            sample(undated, 4),
            "https://developers.google.com/search/docs/appearance/publication-dates",
        )
