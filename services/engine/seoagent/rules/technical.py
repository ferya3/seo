"""HTTP status, redirects, HTTPS, robots.txt, sitemaps and URL hygiene."""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import urlparse

from ..fetcher import normalize_url
from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

DOCS_ROBOTS = "https://developers.google.com/search/docs/crawling-indexing/robots/intro"
DOCS_SITEMAP = "https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap"
DOCS_HTTPS = "https://developers.google.com/search/docs/crawling-indexing/https"

MAX_URL_LENGTH = 115


@rule("status-codes")
def check_status_codes(ctx: SiteContext) -> Iterator[Issue]:
    server_errors, not_found, forbidden, unreachable = [], [], [], []

    for page in ctx.pages:
        if page.error:
            unreachable.append(f"{page.url} ({page.error})")
        elif page.status_code >= 500:
            server_errors.append(f"{page.url} ({page.status_code})")
        elif page.status_code in (404, 410):
            not_found.append(f"{page.url} ({page.status_code})")
        elif page.status_code in (401, 403):
            forbidden.append(f"{page.url} ({page.status_code})")

    if server_errors:
        yield make_issue(
            "server-error",
            "Server errors (5xx)",
            "خطای سرور (5xx)",
            f"{len(server_errors)} آدرس با خطای سرور پاسخ داد. گوگل صفحات ۵xx را از ایندکس "
            "خارج می‌کند و اگر ادامه‌دار باشد سرعت خزش کل سایت را کم می‌کند.",
            "لاگ سرور را برای این آدرس‌ها بررسی کن. تا وقت رفع مشکل، اگر قطعی موقتی است "
            "کد ۵۰۳ با هدر Retry-After برگردان تا گوگل بعداً دوباره تلاش کند.",
            Severity.CRITICAL,
            Category.TECHNICAL,
            server_errors,
            sample(server_errors, 5),
            "https://developers.google.com/search/docs/crawling-indexing/http-network-errors",
        )

    if not_found:
        yield make_issue(
            "broken-internal-links",
            "Internal links to 404 pages",
            "لینک داخلی به صفحات ۴۰۴",
            f"{len(not_found)} آدرس داخلی که از صفحات سایت به آن‌ها لینک داده شده، ۴۰۴ برمی‌گرداند. "
            "این هم تجربه‌ی کاربر را خراب می‌کند و هم اعتبار لینک داخلی را هدر می‌دهد.",
            "لینک‌ها را به آدرس درست اصلاح کن. اگر صفحه واقعاً حذف شده و جایگزین مرتبط دارد، "
            "با ۳۰۱ به جایگزین منتقلش کن؛ اگر جایگزینی ندارد ۴۱۰ برگردان و لینک‌ها را حذف کن.",
            Severity.HIGH,
            Category.LINKS,
            not_found,
            sample(not_found, 5),
            "https://developers.google.com/search/docs/crawling-indexing/http-network-errors",
        )

    if forbidden:
        yield make_issue(
            "forbidden-pages",
            "Pages returning 401/403",
            "صفحات با پاسخ ۴۰۱ یا ۴۰۳",
            f"{len(forbidden)} آدرس دسترسی را رد کرد. اگر این صفحات باید عمومی باشند، "
            "گوگل هم مثل ما نمی‌تواند آن‌ها را ببیند و ایندکس نمی‌شوند.",
            "تنظیمات دسترسی سرور، فایروال یا CDN را بررسی کن. اگر بلاک‌کردن ربات‌ها عمدی است، "
            "مطمئن شو Googlebot در لیست مجاز است.",
            Severity.HIGH,
            Category.INDEXING,
            forbidden,
            sample(forbidden, 4),
            DOCS_ROBOTS,
        )

    if unreachable:
        yield make_issue(
            "unreachable-pages",
            "Unreachable URLs",
            "آدرس‌های غیرقابل دسترس",
            f"{len(unreachable)} آدرس اصلاً پاسخ نداد (تایم‌اوت، خطای DNS یا خطای TLS). "
            "این معمولاً یعنی سرور کند است یا گواهی SSL مشکل دارد.",
            "پایداری سرور و اعتبار گواهی SSL را بررسی کن. تایم‌اوت‌های مکرر باعث افت شدید نرخ خزش می‌شود.",
            Severity.CRITICAL,
            Category.TECHNICAL,
            unreachable,
            sample(unreachable, 4),
            "",
        )


@rule("redirects")
def check_redirects(ctx: SiteContext) -> Iterator[Issue]:
    chains, temporary = [], []
    for page in ctx.pages:
        if not page.redirect_chain:
            continue
        if len(page.redirect_chain) > 1:
            hops = " → ".join(str(code) for code, _ in page.redirect_chain)
            chains.append(f"{page.url} ({len(page.redirect_chain)} پرش: {hops})")
        if any(code in (302, 307) for code, _ in page.redirect_chain):
            temporary.append(f"{page.url} ({page.redirect_chain[0][0]})")

    if chains:
        yield make_issue(
            "redirect-chain",
            "Redirect chains",
            "زنجیره‌ی ریدایرکت",
            f"{len(chains)} آدرس بیش از یک بار ریدایرکت می‌شود. هر پرش اضافه هم زمان بارگذاری "
            "را زیاد می‌کند و هم بودجه‌ی خزش را مصرف می‌کند.",
            "ریدایرکت‌ها را تخت کن: هر آدرس قدیمی باید مستقیم و با یک پرش به مقصد نهایی برود.",
            Severity.MEDIUM,
            Category.TECHNICAL,
            chains,
            sample(chains, 4),
            "https://developers.google.com/search/docs/crawling-indexing/301-redirects",
        )

    if temporary:
        yield make_issue(
            "temporary-redirect",
            "Temporary (302/307) redirects",
            "ریدایرکت موقت (۳۰۲/۳۰۷)",
            f"{len(temporary)} آدرس با ریدایرکت موقت منتقل می‌شود. ریدایرکت موقت به گوگل "
            "می‌گوید آدرس اصلی همان قدیمی است، پس اعتبار لینک به‌طور کامل به مقصد جدید منتقل نمی‌شود.",
            "اگر انتقال دائمی است ریدایرکت را به ۳۰۱ تغییر بده. ۳۰۲ را فقط برای حالت‌های "
            "واقعاً موقت (تست A/B، صفحه‌ی در دست تعمیر) نگه دار.",
            Severity.MEDIUM,
            Category.TECHNICAL,
            temporary,
            sample(temporary, 4),
            "https://developers.google.com/search/docs/crawling-indexing/301-redirects",
        )


LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", ".local", ".test", ".localhost")


def _is_local(origin: str) -> bool:
    host = urlparse(origin).hostname or ""
    return host.startswith(("192.168.", "10.")) or host.endswith(LOCAL_HOSTS) or host in LOCAL_HOSTS


@rule("https")
def check_https(ctx: SiteContext) -> Iterator[Issue]:
    # A local dev server on http is expected, not an SEO failure.
    if _is_local(ctx.origin):
        return

    if urlparse(ctx.origin).scheme != "https":
        yield make_issue(
            "no-https",
            "Site is not served over HTTPS",
            "سایت روی HTTPS نیست",
            "سایت با پروتکل http سرو می‌شود. HTTPS از سال ۲۰۱۴ یک سیگنال رتبه‌بندی تأییدشده است "
            "و مرورگرها سایت http را «ناامن» علامت می‌زنند که مستقیماً نرخ تبدیل را خراب می‌کند.",
            "یک گواهی SSL نصب کن (Let's Encrypt رایگان است) و کل ترافیک http را با ۳۰۱ به https "
            "منتقل کن. بعد هدر HSTS را فعال کن.",
            Severity.CRITICAL,
            Category.TECHNICAL,
            [ctx.start_url],
            "",
            DOCS_HTTPS,
        )
        return

    mixed = []
    for page in ctx.html_pages:
        insecure = re.findall(r'(?:src|href)=["\']http://[^"\']+', page.html)
        insecure = [u for u in insecure if "http://localhost" not in u and "schema.org" not in u]
        if insecure:
            mixed.append(f"{page.url} ({len(insecure)} منبع http)")

    if mixed:
        yield make_issue(
            "mixed-content",
            "Mixed content on HTTPS pages",
            "محتوای ترکیبی روی صفحات HTTPS",
            f"{len(mixed)} صفحه‌ی https منابعی را از http بارگذاری می‌کند. مرورگر این منابع را "
            "بلاک یا با هشدار بارگذاری می‌کند و قفل امنیتی از نوار آدرس حذف می‌شود.",
            "همه‌ی آدرس‌های منابع را به https تغییر بده. اگر منبع خارجی https ندارد، جایگزینش کن "
            "یا فایل را روی سرور خودت میزبانی کن.",
            Severity.HIGH,
            Category.TECHNICAL,
            mixed,
            sample(mixed, 4),
            DOCS_HTTPS,
        )

    homepage = ctx.pages[0] if ctx.pages else None
    if homepage and homepage.headers and "strict-transport-security" not in homepage.headers:
        yield make_issue(
            "no-hsts",
            "HSTS header not set",
            "هدر HSTS تنظیم نشده",
            "هدر Strict-Transport-Security وجود ندارد. بدون آن، اولین درخواست کاربر می‌تواند "
            "روی http انجام شود و در معرض حمله‌ی مرد میانی قرار بگیرد.",
            "هدر `Strict-Transport-Security: max-age=31536000; includeSubDomains` را روی سرور فعال کن. "
            "اول با max-age کوتاه تست کن تا مطمئن شوی همه‌ی زیردامنه‌ها https دارند.",
            Severity.LOW,
            Category.TECHNICAL,
            [ctx.start_url],
            "",
            DOCS_HTTPS,
        )


@rule("robots-txt")
def check_robots(ctx: SiteContext) -> Iterator[Issue]:
    if ctx.robots_txt is None:
        yield make_issue(
            "robots-missing",
            "No robots.txt",
            "فایل robots.txt وجود ندارد",
            f"درخواست /robots.txt با کد {ctx.robots_status or 'خطا'} پاسخ داد. نبودِ این فایل "
            "خطای بحرانی نیست (گوگل فرض می‌کند همه‌چیز مجاز است)، اما جای اعلام سایت‌مپ و "
            "کنترل خزش را از دست می‌دهی.",
            "یک فایل robots.txt در ریشه‌ی دامنه بساز، آدرس سایت‌مپ را داخلش اعلام کن و مسیرهای "
            "بی‌ارزش (سبد خرید، جستجوی داخلی، صفحات ورود) را Disallow کن.",
            Severity.LOW,
            Category.INDEXING,
            [ctx.start_url],
            "",
            DOCS_ROBOTS,
        )
        return

    lowered = ctx.robots_txt.lower()

    # A global "Disallow: /" is the single most destructive SEO mistake there is.
    blocks_everything = False
    current_agents: list[str] = []
    for raw in lowered.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("user-agent:"):
            current_agents.append(line.split(":", 1)[1].strip())
        elif line.startswith("disallow:"):
            value = line.split(":", 1)[1].strip()
            relevant = any(a in ("*", "googlebot") for a in current_agents)
            if value == "/" and relevant:
                blocks_everything = True
        elif line.startswith("allow:"):
            continue
        else:
            current_agents = []

    if blocks_everything:
        yield make_issue(
            "robots-blocks-site",
            "robots.txt blocks the entire site",
            "robots.txt کل سایت را بلاک کرده",
            "در robots.txt دستور `Disallow: /` برای همه‌ی ربات‌ها یا برای Googlebot وجود دارد. "
            "این یعنی گوگل اجازه‌ی خزش هیچ صفحه‌ای را ندارد و سایت عملاً از نتایج جستجو حذف می‌شود. "
            "این معمولاً باقی‌مانده‌ی تنظیمات محیط تست است که اشتباهاً روی سایت اصلی رفته.",
            "فوراً خط `Disallow: /` را حذف کن. بعد در سرچ کنسول ابزار robots.txt Tester را "
            "اجرا کن و درخواست ایندکس مجدد بده.",
            Severity.CRITICAL,
            Category.INDEXING,
            [ctx.start_url],
            "",
            DOCS_ROBOTS,
        )

    if "sitemap:" not in lowered:
        yield make_issue(
            "robots-no-sitemap",
            "robots.txt does not declare a sitemap",
            "سایت‌مپ در robots.txt اعلام نشده",
            "فایل robots.txt وجود دارد ولی خط Sitemap: ندارد. این ساده‌ترین راه برای معرفی "
            "سایت‌مپ به همه‌ی موتورهای جستجو (نه فقط گوگل) است.",
            "خط `Sitemap: https://دامنه‌ات/sitemap.xml` را به انتهای robots.txt اضافه کن.",
            Severity.LOW,
            Category.INDEXING,
            [ctx.start_url],
            "",
            DOCS_SITEMAP,
        )

    # Blocking CSS/JS breaks Google's rendering of the page.
    if re.search(r"disallow:\s*/?.*\.(css|js)\b", lowered) or re.search(
        r"disallow:\s*/(wp-content|assets|static|dist)/?\s*$", lowered, re.M
    ):
        yield make_issue(
            "robots-blocks-assets",
            "robots.txt may block CSS/JS",
            "احتمال بلاک شدن فایل‌های CSS/JS",
            "به نظر می‌رسد robots.txt مسیر فایل‌های استایل یا اسکریپت را Disallow کرده است. "
            "گوگل صفحه را مثل مرورگر رندر می‌کند؛ اگر به CSS و JS دسترسی نداشته باشد صفحه را "
            "شکسته می‌بیند، موبایل‌فرندلی بودن را تشخیص نمی‌دهد و ممکن است محتوای اصلی را نبیند.",
            "دسترسی به فایل‌های CSS، JS و تصاویر را باز بگذار. با ابزار «URL Inspection» در "
            "سرچ کنسول ببین گوگل صفحه را چطور رندر می‌کند.",
            Severity.HIGH,
            Category.INDEXING,
            [ctx.start_url],
            "",
            DOCS_ROBOTS,
        )


@rule("sitemap")
def check_sitemap(ctx: SiteContext) -> Iterator[Issue]:
    if not ctx.sitemap_urls:
        yield make_issue(
            "sitemap-missing",
            "No XML sitemap found",
            "سایت‌مپ XML پیدا نشد",
            "هیچ سایت‌مپ معتبری در مسیرهای متداول و در robots.txt پیدا نشد. سایت‌مپ برای "
            "سایت‌های بزرگ، سایت‌های جدید و صفحاتی که لینک داخلی کمی دارند نقش تعیین‌کننده "
            "در سرعت کشف صفحات دارد.",
            "یک sitemap.xml بساز که فقط آدرس‌های قابل ایندکس و نهایی (بدون ریدایرکت، بدون noindex) "
            "را شامل شود، در robots.txt اعلامش کن و در سرچ کنسول ثبتش کن.",
            Severity.MEDIUM,
            Category.INDEXING,
            [ctx.start_url],
            "",
            DOCS_SITEMAP,
        )
        return

    if ctx.sitemap_errors:
        yield make_issue(
            "sitemap-invalid",
            "Invalid sitemap response",
            "پاسخ نامعتبر سایت‌مپ",
            "یکی از آدرس‌های سایت‌مپ به‌جای XML صفحه‌ی HTML برگرداند: " + "؛ ".join(ctx.sitemap_errors[:3]),
            "مطمئن شو سایت‌مپ با Content-Type برابر application/xml سرو می‌شود و آدرسش در "
            "robots.txt درست است.",
            Severity.MEDIUM,
            Category.INDEXING,
            ctx.sitemap_errors[:10],
            "",
            DOCS_SITEMAP,
        )

    crawled = {normalize_url(p.url) for p in ctx.pages}
    sitemap_set = {normalize_url(u) for u in ctx.sitemap_locs}

    # Sitemap entries that turned out to be non-200 or noindex.
    bad_entries = []
    for page in ctx.pages:
        if normalize_url(page.url) not in sitemap_set:
            continue
        if page.error or page.status_code >= 400:
            bad_entries.append(f"{page.url} ({page.status_code or page.error})")
        elif page.redirect_chain:
            bad_entries.append(f"{page.url} (ریدایرکت می‌شود)")
        elif page.is_noindex:
            bad_entries.append(f"{page.url} (noindex)")

    if bad_entries:
        yield make_issue(
            "sitemap-bad-entries",
            "Sitemap contains non-indexable URLs",
            "سایت‌مپ آدرس‌های غیرقابل ایندکس دارد",
            f"{len(bad_entries)} آدرس داخل سایت‌مپ یا خطا می‌دهد، یا ریدایرکت می‌شود، یا noindex است. "
            "سایت‌مپ به گوگل می‌گوید «این‌ها صفحات مهم من هستند»؛ آدرس‌های بی‌ارزش داخلش اعتماد "
            "به کل سایت‌مپ را کم می‌کند.",
            "سایت‌مپ را طوری بازتولید کن که فقط آدرس‌های نهایی با کد ۲۰۰ و قابل ایندکس داخلش باشد. "
            "این کار را به فرایند خودکار تولید سایت‌مپ بسپار، نه به‌روزرسانی دستی.",
            Severity.MEDIUM,
            Category.INDEXING,
            bad_entries,
            sample(bad_entries, 4),
            DOCS_SITEMAP,
        )

    # Pages we discovered by crawling that never appear in the sitemap.
    orphaned_from_sitemap = [
        p.url for p in ctx.indexable_pages if normalize_url(p.url) not in sitemap_set
    ]
    if sitemap_set and len(orphaned_from_sitemap) > max(2, len(ctx.indexable_pages) * 0.2):
        yield make_issue(
            "sitemap-incomplete",
            "Indexable pages missing from sitemap",
            "صفحات قابل ایندکس در سایت‌مپ نیستند",
            f"{len(orphaned_from_sitemap)} صفحه‌ی قابل ایندکس که با خزش پیدا شدند در سایت‌مپ نیامده‌اند.",
            "فرایند تولید سایت‌مپ را بررسی کن؛ باید همه‌ی صفحات قابل ایندکس را به‌صورت خودکار پوشش بدهد.",
            Severity.LOW,
            Category.INDEXING,
            orphaned_from_sitemap,
            sample(orphaned_from_sitemap, 4),
            DOCS_SITEMAP,
        )

    if crawled and not (crawled & sitemap_set) and sitemap_set:
        yield make_issue(
            "sitemap-mismatch",
            "Sitemap URLs do not match crawled URLs",
            "آدرس‌های سایت‌مپ با آدرس‌های واقعی سایت هم‌خوانی ندارد",
            "هیچ‌کدام از آدرس‌های داخل سایت‌مپ با آدرس‌هایی که در خزش پیدا شد یکی نیست. "
            "معمولاً یعنی سایت‌مپ آدرس‌ها را با پروتکل، دامنه یا اسلش انتهایی متفاوت لیست می‌کند.",
            "آدرس‌های داخل سایت‌مپ را دقیقاً برابر با نسخه‌ی canonical صفحات بنویس "
            "(همان پروتکل، همان www یا بدون www، همان اسلش انتهایی).",
            Severity.MEDIUM,
            Category.INDEXING,
            [ctx.start_url],
            f"نمونه سایت‌مپ: {sample(list(sitemap_set), 2)}",
            DOCS_SITEMAP,
        )


@rule("mobile")
def check_mobile(ctx: SiteContext) -> Iterator[Issue]:
    missing_viewport, bad_viewport = [], []
    for page in ctx.html_pages:
        viewport = (page.viewport or "").lower()
        if not viewport:
            missing_viewport.append(page.url)
        elif "width=device-width" not in viewport:
            bad_viewport.append(f"{page.url} ({page.viewport})")
        elif "user-scalable=no" in viewport or re.search(r"maximum-scale=1(\.0)?\b", viewport):
            bad_viewport.append(f"{page.url} (بزرگ‌نمایی غیرفعال است)")

    if missing_viewport:
        yield make_issue(
            "viewport-missing",
            "Missing viewport meta tag",
            "تگ viewport وجود ندارد",
            f"{len(missing_viewport)} صفحه تگ viewport ندارد. گوگل از جولای ۲۰۲۴ فقط با "
            "Googlebot موبایل ایندکس می‌کند؛ صفحه‌ی بدون viewport روی موبایل زوم‌شده و "
            "غیرقابل استفاده رندر می‌شود.",
            'تگ `<meta name="viewport" content="width=device-width, initial-scale=1">` را به head همه‌ی صفحات اضافه کن.',
            Severity.CRITICAL,
            Category.TECHNICAL,
            missing_viewport,
            sample(missing_viewport, 4),
            "https://developers.google.com/search/docs/crawling-indexing/mobile/mobile-sites-mobile-first-indexing",
        )

    if bad_viewport:
        yield make_issue(
            "viewport-invalid",
            "Viewport configured incorrectly",
            "تنظیمات viewport اشتباه است",
            f"{len(bad_viewport)} صفحه viewport دارد اما درست تنظیم نشده (نبود width=device-width "
            "یا غیرفعال کردن بزرگ‌نمایی). غیرفعال‌کردن زوم مشکل دسترس‌پذیری است.",
            'مقدار را به `width=device-width, initial-scale=1` تغییر بده و user-scalable=no را بردار.',
            Severity.MEDIUM,
            Category.TECHNICAL,
            bad_viewport,
            sample(bad_viewport, 3),
            "https://developers.google.com/search/docs/crawling-indexing/mobile/mobile-sites-mobile-first-indexing",
        )


@rule("url-structure")
def check_urls(ctx: SiteContext) -> Iterator[Issue]:
    too_long, ugly, uppercase, underscores = [], [], [], []

    for page in ctx.indexable_pages:
        parsed = urlparse(page.url)
        path = parsed.path
        if len(page.url) > MAX_URL_LENGTH:
            too_long.append(f"{page.url} ({len(page.url)} کاراکتر)")
        if parsed.query and len(parsed.query.split("&")) >= 3:
            ugly.append(page.url)
        if any(c.isupper() for c in path):
            uppercase.append(page.url)
        if "_" in path:
            underscores.append(page.url)

    if too_long:
        yield make_issue(
            "url-too-long",
            "Overly long URLs",
            "آدرس‌های خیلی بلند",
            f"{len(too_long)} آدرس بلندتر از {MAX_URL_LENGTH} کاراکتر است. آدرس بلند در نتایج "
            "جستجو بریده می‌شود، اشتراک‌گذاری‌اش سخت است و معمولاً نشانه‌ی ساختار دسته‌بندی بیش‌ازحد تودرتو است.",
            "آدرس‌ها را کوتاه و خوانا کن: سه تا پنج کلمه‌ی کلیدی، بدون کلمات اضافه، حداکثر دو سطح عمق.",
            Severity.LOW,
            Category.TECHNICAL,
            too_long,
            sample(too_long, 3),
            "https://developers.google.com/search/docs/crawling-indexing/url-structure",
        )

    if underscores:
        yield make_issue(
            "url-underscores",
            "Underscores in URLs",
            "آندرلاین در آدرس صفحات",
            f"{len(underscores)} آدرس از _ به‌عنوان جداکننده استفاده می‌کند. گوگل صراحتاً "
            "خط تیره (-) را توصیه می‌کند چون کلمات را جدا می‌بیند، در حالی که آندرلاین آن‌ها را به هم می‌چسباند.",
            "برای صفحات جدید از خط تیره استفاده کن. آدرس‌های قدیمی را فقط در صورتی تغییر بده "
            "که با ۳۰۱ درست ریدایرکت کنی — وگرنه سود تغییر کمتر از ریسکش است.",
            Severity.LOW,
            Category.TECHNICAL,
            underscores,
            sample(underscores, 3),
            "https://developers.google.com/search/docs/crawling-indexing/url-structure",
        )

    if uppercase:
        yield make_issue(
            "url-uppercase",
            "Uppercase letters in URLs",
            "حروف بزرگ در آدرس صفحات",
            f"{len(uppercase)} آدرس حرف بزرگ دارد. سرورهای لینوکسی به بزرگ و کوچک بودن حروف "
            "حساس‌اند، پس /Page و /page می‌توانند دو صفحه‌ی جدا با محتوای یکسان باشند.",
            "همه‌ی آدرس‌ها را کوچک کن و یک قانون ریدایرکت بگذار که نسخه‌های بزرگ‌حرف را با ۳۰۱ به نسخه‌ی کوچک ببرد.",
            Severity.LOW,
            Category.TECHNICAL,
            uppercase,
            sample(uppercase, 3),
            "https://developers.google.com/search/docs/crawling-indexing/url-structure",
        )

    if ugly:
        yield make_issue(
            "url-parameters",
            "URLs with many query parameters",
            "آدرس‌های پارامتری",
            f"{len(ugly)} آدرس سه پارامتر یا بیشتر دارد. آدرس‌های پارامتری بی‌نهایت ترکیب تولید "
            "می‌کنند و می‌توانند بودجه‌ی خزش را کاملاً بسوزانند (تله‌ی خزش).",
            "برای صفحات مهم آدرس تمیز و ثابت بساز. ترکیب‌های فیلتر را با canonical به نسخه‌ی "
            "اصلی اشاره بده و ترکیب‌های بی‌ارزش را در robots.txt ببند.",
            Severity.MEDIUM,
            Category.INDEXING,
            ugly,
            sample(ugly, 3),
            "https://developers.google.com/search/docs/crawling-indexing/url-structure",
        )


@rule("lang-charset")
def check_lang(ctx: SiteContext) -> Iterator[Issue]:
    missing_lang, missing_charset = [], []
    for page in ctx.html_pages:
        if not page.lang:
            missing_lang.append(page.url)
        content_type = page.headers.get("content-type", "").lower()
        if not page.charset and "charset" not in content_type:
            missing_charset.append(page.url)

    if missing_lang:
        yield make_issue(
            "html-lang-missing",
            "Missing lang attribute",
            "ویژگی lang در تگ html نیست",
            f"{len(missing_lang)} صفحه ویژگی lang روی تگ <html> ندارد. این ویژگی به گوگل و به "
            "صفحه‌خوان‌ها می‌گوید صفحه به چه زبانی است — برای سایت فارسی که کلمات انگلیسی هم "
            "دارد، تشخیص خودکار زبان همیشه درست کار نمی‌کند.",
            'برای سایت فارسی بنویس `<html lang="fa" dir="rtl">`.',
            Severity.MEDIUM,
            Category.INTERNATIONAL,
            missing_lang,
            sample(missing_lang, 4),
            "https://developers.google.com/search/docs/specialty/international/localized-versions",
        )

    if missing_charset:
        yield make_issue(
            "charset-missing",
            "Character encoding not declared",
            "انکودینگ کاراکترها اعلام نشده",
            f"{len(missing_charset)} صفحه انکودینگ را نه در تگ meta و نه در هدر HTTP اعلام نکرده. "
            "برای متن فارسی این یعنی ریسک جدی نمایش کاراکترهای درهم (□□□).",
            'در ابتدای <head> بنویس `<meta charset="utf-8">` — باید در ۱۰۲۴ بایت اول صفحه باشد.',
            Severity.MEDIUM,
            Category.TECHNICAL,
            missing_charset,
            sample(missing_charset, 3),
            "",
        )


@rule("compression")
def check_compression(ctx: SiteContext) -> Iterator[Issue]:
    uncompressed = []
    for page in ctx.html_pages:
        encoding = page.headers.get("content-encoding", "").lower()
        if not encoding and page.byte_size > 50_000:
            uncompressed.append(f"{page.url} ({page.byte_size // 1024} کیلوبایت، بدون فشرده‌سازی)")

    if uncompressed:
        yield make_issue(
            "no-compression",
            "HTML served without compression",
            "HTML بدون فشرده‌سازی سرو می‌شود",
            f"{len(uncompressed)} صفحه‌ی حجیم بدون gzip یا brotli فرستاده می‌شود. فشرده‌سازی "
            "معمولاً حجم HTML را ۷۰ تا ۸۰ درصد کم می‌کند و مستقیماً روی LCP اثر می‌گذارد.",
            "روی سرور یا CDN فشرده‌سازی brotli (یا حداقل gzip) را برای text/html، CSS و JS فعال کن.",
            Severity.MEDIUM,
            Category.PERFORMANCE,
            uncompressed,
            sample(uncompressed, 3),
            "https://developer.chrome.com/docs/lighthouse/performance/uses-text-compression",
        )
