"""Core Web Vitals — real field data when available, static heuristics otherwise."""

from __future__ import annotations

from collections.abc import Iterator

from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

HEAVY_PAGE_BYTES = 500_000
SLOW_RESPONSE_MS = 800
MANY_SCRIPTS = 15

DOCS_CWV = "https://web.dev/articles/vitals"


@rule("core-web-vitals")
def check_field_data(ctx: SiteContext) -> Iterator[Issue]:
    psi = ctx.psi
    if not psi or psi.get("error") or not psi.get("field"):
        return

    severity_map = {"poor": Severity.HIGH, "needs-improvement": Severity.MEDIUM}
    fixes = {
        "LCP": "بزرگ‌ترین عنصر بالای صفحه (معمولاً تصویر شاخص یا تیتر) را سریع‌تر کن: تصویر را "
               "با fetchpriority=\"high\" و بدون lazy بارگذاری کن، فونت‌ها را preload کن، و "
               "زمان پاسخ سرور (TTFB) را با کش کردن پایین بیاور.",
        "INP": "جاوااسکریپت سنگین را بشکن: کارهای طولانی را به تکه‌های کوچک تقسیم کن، اسکریپت‌های "
               "شخص ثالث (چت آنلاین، تبلیغات، آنالیتیکس) را با defer یا بعد از تعامل کاربر بارگذاری کن، "
               "و از هندلرهای سنگین روی رویدادهای پرتکرار پرهیز کن.",
        "CLS": "برای تصاویر و ویدیوها width و height بگذار، برای تبلیغات و ویجت‌ها فضای ثابت رزرو کن، "
               "و فونت‌ها را با font-display: swap و preload بارگذاری کن تا متن جابه‌جا نشود.",
        "FCP": "منابع مسدودکننده‌ی رندر را حذف کن، CSS بحرانی را inline کن و بقیه را با تأخیر بارگذاری کن.",
        "TTFB": "پاسخ سرور را با کش صفحه، CDN و بهینه‌سازی کوئری‌های دیتابیس سریع‌تر کن.",
    }

    for short, data in psi["field"].items():
        if data["status"] == "good":
            continue
        unit = f" {data['unit']}" if data["unit"] else ""
        threshold = data["good_threshold"]
        status_fa = "ضعیف" if data["status"] == "poor" else "نیازمند بهبود"
        yield make_issue(
            f"cwv-{short.lower()}",
            f"Core Web Vital {short} is {data['status']}",
            f"معیار {short} ({data['label_fa']}) وضعیت {status_fa} دارد",
            f"داده‌ی واقعی کاربران گوگل (CrUX) برای این آدرس مقدار {data['value']:g}{unit} را نشان "
            f"می‌دهد؛ حد «خوب» {threshold:g}{unit} است. Core Web Vitals بخشی از سیگنال‌های "
            "تجربه‌ی صفحه است و در رقابت‌های نزدیک تعیین‌کننده می‌شود.",
            fixes.get(short, "گزارش PageSpeed Insights همین آدرس را برای فهرست دقیق اقدامات ببین."),
            severity_map.get(data["status"], Severity.MEDIUM),
            Category.PERFORMANCE,
            [ctx.start_url],
            f"مقدار صدک ۷۵ کاربران: {data['value']:g}{unit}",
            DOCS_CWV,
        )

    for opportunity in psi.get("opportunities", [])[:4]:
        yield make_issue(
            f"psi-{opportunity['id']}",
            f"PageSpeed opportunity: {opportunity['title']}",
            f"فرصت بهبود سرعت: {opportunity['title']}",
            f"لایت‌هاوس تخمین می‌زند رفع این مورد حدود {opportunity['savings_ms']} میلی‌ثانیه از "
            "زمان بارگذاری کم می‌کند.",
            "جزئیات و فهرست فایل‌های دخیل را در گزارش PageSpeed Insights همین آدرس ببین.",
            Severity.LOW,
            Category.PERFORMANCE,
            [ctx.start_url],
            "",
            "https://pagespeed.web.dev/",
        )


@rule("page-weight")
def check_page_weight(ctx: SiteContext) -> Iterator[Issue]:
    heavy, slow, script_heavy, inline_heavy = [], [], [], []

    for page in ctx.html_pages:
        if page.byte_size > HEAVY_PAGE_BYTES:
            heavy.append(f"{page.url} ({page.byte_size // 1024} کیلوبایت HTML)")
        if page.elapsed_ms > SLOW_RESPONSE_MS:
            slow.append(f"{page.url} ({page.elapsed_ms} میلی‌ثانیه)")
        if page.external_script_count > MANY_SCRIPTS:
            script_heavy.append(f"{page.url} ({page.external_script_count} اسکریپت خارجی)")
        if page.inline_script_bytes > 100_000:
            inline_heavy.append(f"{page.url} ({page.inline_script_bytes // 1024} کیلوبایت اسکریپت درون‌خطی)")

    if heavy:
        yield make_issue(
            "heavy-html",
            "Very large HTML documents",
            "سند HTML بیش از حد سنگین",
            f"{len(heavy)} صفحه بیش از {HEAVY_PAGE_BYTES // 1024} کیلوبایت HTML خام دارد. "
            "HTML سنگین یعنی مرورگر دیرتر شروع به رندر می‌کند و LCP بدتر می‌شود. معمولاً "
            "دلیلش داده‌ی درون‌خطی زیاد یا DOM بسیار بزرگ است.",
            "داده‌های درون‌خطی و CSS/JS تزریق‌شده را به فایل‌های جدا با قابلیت کش منتقل کن، "
            "و اگر فهرست بلندی رندر می‌شود از صفحه‌بندی یا بارگذاری تدریجی استفاده کن.",
            Severity.MEDIUM,
            Category.PERFORMANCE,
            heavy,
            sample(heavy, 3),
            DOCS_CWV,
        )

    if slow:
        yield make_issue(
            "slow-response",
            "Slow server response (TTFB)",
            "پاسخ کند سرور",
            f"{len(slow)} صفحه بیش از {SLOW_RESPONSE_MS} میلی‌ثانیه طول کشید تا پاسخ بدهد. "
            "زمان پاسخ سرور کف زمان بارگذاری است: هر میلی‌ثانیه اینجا مستقیماً به LCP اضافه می‌شود. "
            "گوگل TTFB زیر ۸۰۰ میلی‌ثانیه را توصیه می‌کند.",
            "کش صفحه در سمت سرور را فعال کن، از CDN استفاده کن، کوئری‌های کند دیتابیس را "
            "بهینه کن و اگر سرور در خارج از ایران است سرعت مسیر شبکه را هم بسنج.",
            Severity.HIGH,
            Category.PERFORMANCE,
            slow,
            sample(slow, 4),
            "https://web.dev/articles/ttfb",
        )

    if script_heavy:
        yield make_issue(
            "too-many-scripts",
            "Too many external scripts",
            "تعداد زیاد اسکریپت خارجی",
            f"{len(script_heavy)} صفحه بیش از {MANY_SCRIPTS} فایل اسکریپت خارجی بارگذاری می‌کند. "
            "هر اسکریپت شخص ثالث هم زمان بارگذاری را زیاد می‌کند و هم رشته‌ی اصلی را مشغول "
            "می‌کند که مستقیماً معیار INP را خراب می‌کند.",
            "فهرست اسکریپت‌ها را مرور کن و هرچه واقعاً لازم نیست حذف کن. بقیه را با defer "
            "بارگذاری کن و ابزارهای سنگین (چت، نقشه، ویدیو) را فقط بعد از تعامل کاربر لود کن.",
            Severity.MEDIUM,
            Category.PERFORMANCE,
            script_heavy,
            sample(script_heavy, 3),
            "https://web.dev/articles/inp",
        )

    if inline_heavy:
        yield make_issue(
            "large-inline-script",
            "Large inline scripts",
            "اسکریپت درون‌خطی حجیم",
            f"{len(inline_heavy)} صفحه اسکریپت درون‌خطی بسیار بزرگی دارد. اسکریپت درون‌خطی "
            "قابل کش شدن نیست، پس در هر بازدید دوباره دانلود می‌شود.",
            "این کدها را به فایل .js جدا منتقل کن تا مرورگر بتواند کششان کند.",
            Severity.LOW,
            Category.PERFORMANCE,
            inline_heavy,
            sample(inline_heavy, 3),
            DOCS_CWV,
        )


@rule("render-blocking")
def check_render_blocking(ctx: SiteContext) -> Iterator[Issue]:
    offenders = []
    for page in ctx.html_pages:
        total = page.render_blocking_scripts + page.render_blocking_styles
        if total >= 4:
            offenders.append(
                f"{page.url} ({page.render_blocking_scripts} اسکریپت + {page.render_blocking_styles} استایل)"
            )

    if offenders:
        yield make_issue(
            "render-blocking-resources",
            "Render-blocking resources in <head>",
            "منابع مسدودکننده‌ی رندر در head",
            f"{len(offenders)} صفحه چند فایل CSS/JS مسدودکننده در head دارد. مرورگر تا دانلود "
            "و اجرای کامل این فایل‌ها هیچ چیزی روی صفحه نشان نمی‌دهد، یعنی FCP و LCP هر دو عقب می‌افتند.",
            "به تگ‌های script در head ویژگی `defer` (یا `async` برای اسکریپت‌های مستقل) اضافه کن. "
            "برای CSS، استایل‌های بحرانی بالای صفحه را inline کن و بقیه را با "
            "`<link rel=\"preload\" as=\"style\" onload=\"this.rel='stylesheet'\">` با تأخیر بارگذاری کن.",
            Severity.MEDIUM,
            Category.PERFORMANCE,
            offenders,
            sample(offenders, 3),
            "https://developer.chrome.com/docs/lighthouse/performance/render-blocking-resources",
        )


@rule("caching-headers")
def check_caching(ctx: SiteContext) -> Iterator[Issue]:
    homepage = ctx.pages[0] if ctx.pages else None
    if not homepage or not homepage.headers:
        return
    cache_control = homepage.headers.get("cache-control", "").lower()
    if not cache_control or "no-store" in cache_control:
        yield make_issue(
            "no-cache-headers",
            "No caching headers",
            "هدر کش تنظیم نشده",
            "پاسخ سرور هدر Cache-Control معناداری ندارد. بدون آن، مرورگر و CDN نمی‌دانند "
            "چه چیزی را چقدر نگه دارند و بازدیدهای بعدی همان کاربر هم کند خواهد بود.",
            "برای فایل‌های ثابت (تصویر، CSS، JS با نام هش‌دار) هدر "
            "`Cache-Control: public, max-age=31536000, immutable` و برای HTML مقدار کوتاه‌تر "
            "همراه با ETag تنظیم کن.",
            Severity.LOW,
            Category.PERFORMANCE,
            [ctx.start_url],
            f"مقدار فعلی: {cache_control or 'ندارد'}",
            "https://web.dev/articles/http-cache",
        )
