"""Image SEO: alt text, layout stability, lazy loading and modern formats."""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import urlparse

from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

MODERN_FORMATS = (".webp", ".avif", ".svg")
LEGACY_FORMATS = (".jpg", ".jpeg", ".png", ".gif", ".bmp")

DOCS_IMAGES = "https://developers.google.com/search/docs/appearance/google-images"


@rule("image-alt")
def check_alt_text(ctx: SiteContext) -> Iterator[Issue]:
    missing, stuffed = [], []

    for page in ctx.html_pages:
        page_missing = [i for i in page.images if i.alt is None]
        if page_missing:
            missing.append(f"{page.url} ({len(page_missing)} از {len(page.images)} تصویر)")
        for image in page.images:
            if image.alt and len(image.alt) > 125:
                stuffed.append(f"{page.url} ({len(image.alt)} کاراکتر alt)")
                break

    if missing:
        yield make_issue(
            "image-alt-missing",
            "Images without alt text",
            "تصاویر بدون متن جایگزین (alt)",
            f"در {len(missing)} صفحه تصاویری بدون ویژگی alt وجود دارد. alt هم برای رتبه‌گرفتن در "
            "جستجوی تصاویر گوگل لازم است، هم برای کاربران صفحه‌خوان، و هم به گوگل کمک می‌کند "
            "موضوع صفحه را بهتر بفهمد.",
            "برای هر تصویر معنادار یک alt توصیفی بنویس که واقعاً محتوای تصویر را توضیح بدهد "
            "(نه فقط کلمه کلیدی). برای تصاویر صرفاً تزئینی `alt=\"\"` خالی بگذار تا صفحه‌خوان ردشان کند.",
            Severity.MEDIUM,
            Category.IMAGES,
            missing,
            sample(missing, 4),
            DOCS_IMAGES,
        )

    if stuffed:
        yield make_issue(
            "image-alt-too-long",
            "Alt text too long",
            "متن جایگزین بیش از حد بلند",
            f"{len(stuffed)} صفحه تصویری با alt بلندتر از ۱۲۵ کاراکتر دارد. alt خیلی بلند "
            "معمولاً یعنی داخلش کلمه کلیدی چپانده شده که مصداق اسپم است.",
            "alt را به یک جمله‌ی کوتاه و توصیفی کوتاه کن. اگر توضیح مفصل لازم است، آن را در "
            "کپشن تصویر یا متن اطراف بیاور.",
            Severity.LOW,
            Category.IMAGES,
            stuffed,
            sample(stuffed, 3),
            DOCS_IMAGES,
        )


@rule("image-cls")
def check_layout_shift(ctx: SiteContext) -> Iterator[Issue]:
    """Images without explicit dimensions are the number one cause of CLS."""
    offenders = []
    for page in ctx.html_pages:
        undimensioned = [
            i for i in page.images
            if i.src and (not i.width or not i.height) and not i.src.lower().endswith(".svg")
        ]
        if undimensioned:
            offenders.append(f"{page.url} ({len(undimensioned)} تصویر بدون ابعاد)")

    if offenders:
        yield make_issue(
            "image-no-dimensions",
            "Images without width/height",
            "تصاویر بدون تعیین عرض و ارتفاع",
            f"در {len(offenders)} صفحه تصاویری بدون ویژگی width و height وجود دارد. مرورگر "
            "تا وقتی تصویر دانلود نشده نمی‌داند چقدر جا بگیرد، پس بعد از بارگذاری محتوا "
            "جابه‌جا می‌شود. این مستقیم‌ترین دلیل بد شدن معیار CLS است که یکی از سه Core Web Vital است.",
            "روی هر تگ img مقادیر width و height واقعی را بنویس و در CSS `height: auto` بگذار "
            "تا نسبت تصویر حفظ شود. مرورگر از این دو عدد فضای لازم را از قبل رزرو می‌کند.",
            Severity.MEDIUM,
            Category.PERFORMANCE,
            offenders,
            sample(offenders, 4),
            "https://web.dev/articles/cls",
        )


@rule("image-lazy-loading")
def check_lazy_loading(ctx: SiteContext) -> Iterator[Issue]:
    lcp_lazy, no_lazy = [], []

    for page in ctx.html_pages:
        for image in page.images:
            if image.is_in_first_viewport and (image.loading or "").lower() == "lazy":
                lcp_lazy.append(f"{page.url} ({image.src[:80]})")
                break
        below_fold = [i for i in page.images[3:] if not i.loading]
        if len(below_fold) >= 5:
            no_lazy.append(f"{page.url} ({len(below_fold)} تصویر بدون lazy loading)")

    if lcp_lazy:
        yield make_issue(
            "lcp-image-lazy",
            "Above-the-fold image is lazy-loaded",
            "تصویر بالای صفحه lazy بارگذاری می‌شود",
            f"{len(lcp_lazy)} صفحه تصویر ابتدای صفحه را با loading=\"lazy\" بارگذاری می‌کند. "
            "این تصویر معمولاً همان عنصر LCP است و lazy کردنش عملاً بارگذاری‌اش را عقب می‌اندازد "
            "و LCP را بدتر می‌کند — یعنی دقیقاً برعکس چیزی که می‌خواستی.",
            "برای تصویر اصلی بالای صفحه از `loading=\"eager\"` و `fetchpriority=\"high\"` استفاده کن "
            "و lazy را فقط برای تصاویر پایین‌تر از تای صفحه بگذار.",
            Severity.MEDIUM,
            Category.PERFORMANCE,
            lcp_lazy,
            sample(lcp_lazy, 3),
            "https://web.dev/articles/lcp",
        )

    if no_lazy:
        yield make_issue(
            "no-lazy-loading",
            "Below-the-fold images not lazy-loaded",
            "تصاویر پایین صفحه بدون lazy loading",
            f"{len(no_lazy)} صفحه تصاویر زیادی دارد که همه هم‌زمان بارگذاری می‌شوند. "
            "این پهنای باند را هدر می‌دهد و بارگذاری محتوای اصلی را کند می‌کند.",
            'به تصاویر پایین‌تر از تای صفحه `loading="lazy"` اضافه کن.',
            Severity.LOW,
            Category.PERFORMANCE,
            no_lazy,
            sample(no_lazy, 3),
            "https://web.dev/articles/browser-level-image-lazy-loading",
        )


@rule("image-format")
def check_formats(ctx: SiteContext) -> Iterator[Issue]:
    legacy_pages, bad_filenames = [], []

    for page in ctx.html_pages:
        legacy = [i for i in page.images if i.src.lower().split("?")[0].endswith(LEGACY_FORMATS)]
        modern_present = any(i.src.lower().split("?")[0].endswith(MODERN_FORMATS) for i in page.images)
        has_picture = "<picture" in page.html.lower() or "srcset" in page.html.lower()
        if len(legacy) >= 3 and not modern_present and not has_picture:
            legacy_pages.append(f"{page.url} ({len(legacy)} تصویر jpg/png)")

        for image in page.images:
            name = urlparse(image.src).path.rsplit("/", 1)[-1]
            if re.match(r"^(img|image|photo|dsc|screenshot|untitled)[-_ ]?\d*\.\w+$", name, re.I):
                bad_filenames.append(f"{page.url} ({name})")
                break

    if legacy_pages:
        yield make_issue(
            "legacy-image-format",
            "No modern image formats",
            "استفاده نکردن از فرمت‌های مدرن تصویر",
            f"{len(legacy_pages)} صفحه فقط از jpg/png استفاده می‌کند و هیچ نسخه‌ی WebP یا AVIF "
            "ارائه نمی‌دهد. این فرمت‌ها معمولاً ۲۵ تا ۵۰ درصد حجم کمتری با کیفیت یکسان دارند و "
            "کاهش حجم تصاویر بزرگ‌ترین برد ممکن روی LCP است.",
            "تصاویر را به WebP (یا AVIF) تبدیل کن و با تگ <picture> نسخه‌ی جایگزین jpg را هم "
            "برای مرورگرهای قدیمی نگه دار. همراهش از srcset برای تصاویر ریسپانسیو استفاده کن.",
            Severity.MEDIUM,
            Category.PERFORMANCE,
            legacy_pages,
            sample(legacy_pages, 3),
            "https://developer.chrome.com/docs/lighthouse/performance/uses-webp-images",
        )

    if bad_filenames:
        yield make_issue(
            "image-filename",
            "Non-descriptive image filenames",
            "نام فایل تصاویر بی‌معناست",
            f"{len(bad_filenames)} صفحه تصویری با نام فایل بی‌معنا مثل IMG_1234.jpg دارد. "
            "گوگل نام فایل را به‌عنوان یکی از سیگنال‌های فهم محتوای تصویر استفاده می‌کند.",
            "نام فایل‌ها را توصیفی و با خط تیره بنویس: `kafsh-varzeshi-mardane.webp` "
            "به‌جای `IMG_1234.jpg`.",
            Severity.LOW,
            Category.IMAGES,
            bad_filenames,
            sample(bad_filenames, 3),
            DOCS_IMAGES,
        )
