"""Schema.org / JSON-LD structured data checks.

Google's rich-result catalogue has narrowed over the years (FAQ and HowTo rich
results were heavily restricted in 2023), so this module focuses on the types
that still earn enhancements and on validating what is actually present.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

DOCS_SD = "https://developers.google.com/search/docs/appearance/structured-data/search-gallery"

# Required properties Google checks for the most common types.
REQUIRED_FIELDS: dict[str, list[str]] = {
    "Article": ["headline"],
    "NewsArticle": ["headline"],
    "BlogPosting": ["headline"],
    "Product": ["name"],
    "Recipe": ["name", "image"],
    "Event": ["name", "startDate", "location"],
    "JobPosting": ["title", "datePosted", "hiringOrganization"],
    "LocalBusiness": ["name", "address"],
    "Organization": ["name"],
    "BreadcrumbList": ["itemListElement"],
    "VideoObject": ["name", "thumbnailUrl", "uploadDate"],
    "SoftwareApplication": ["name"],
    "Course": ["name"],
}

# Types Google no longer shows as rich results for most sites.
DEPRECATED_TYPES = {
    "FAQPage": "از اوت ۲۰۲۳ گوگل نتایج غنی FAQ را فقط برای سایت‌های دولتی و سلامت معتبر نشان می‌دهد",
    "HowTo": "نتایج غنی HowTo از سال ۲۰۲۳ کنار گذاشته شد",
}


def _types(block: dict[str, Any]) -> list[str]:
    raw = block.get("@type")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    return []


@rule("structured-data-presence")
def check_presence(ctx: SiteContext) -> Iterator[Issue]:
    pages = ctx.indexable_pages
    if not pages:
        return

    without = [p.url for p in pages if not p.jsonld and not p.microdata_types]
    if len(without) == len(pages):
        yield make_issue(
            "no-structured-data",
            "No structured data at all",
            "هیچ داده ساختاریافته‌ای وجود ندارد",
            "هیچ‌کدام از صفحات JSON-LD یا microdata ندارند. داده ساختاریافته به گوگل می‌گوید "
            "محتوای صفحه دقیقاً چه چیزی است (مقاله، محصول، کسب‌وکار محلی) و شرط لازم برای "
            "نمایش نتایج غنی، ستاره‌ی امتیاز، قیمت و بردکرامب در نتایج است. برای موتورهای "
            "جستجوی مبتنی بر هوش مصنوعی هم مهم‌ترین راه انتقال داده‌ی صریح است.",
            "با JSON-LD شروع کن (فرمت پیشنهادی خود گوگل): در صفحه‌ی اصلی Organization یا "
            "LocalBusiness، در مقالات Article، در صفحات محصول Product و در همه‌ی صفحات "
            "BreadcrumbList. خروجی را با ابزار Rich Results Test گوگل تست کن.",
            Severity.HIGH,
            Category.STRUCTURED_DATA,
            [ctx.start_url],
            "",
            DOCS_SD,
        )
    elif without and len(without) > len(pages) * 0.5:
        yield make_issue(
            "structured-data-partial",
            "Structured data missing on many pages",
            "داده ساختاریافته روی بسیاری از صفحات نیست",
            f"{len(without)} صفحه از {len(pages)} صفحه هیچ داده ساختاریافته‌ای ندارد.",
            "نشانه‌گذاری را در سطح قالب سایت پیاده کن تا همه‌ی صفحات هم‌نوع به‌صورت خودکار پوشش داده شوند.",
            Severity.MEDIUM,
            Category.STRUCTURED_DATA,
            without,
            sample(without, 4),
            DOCS_SD,
        )


@rule("structured-data-validity")
def check_validity(ctx: SiteContext) -> Iterator[Issue]:
    broken, missing_fields, no_type, deprecated = [], [], [], []

    for page in ctx.html_pages:
        for block in page.jsonld:
            if "__parse_error__" in block:
                broken.append(f"{page.url} — {block['__parse_error__'][:80]}")
                continue
            types = _types(block)
            if not types:
                no_type.append(page.url)
                continue
            for type_name in types:
                if type_name in DEPRECATED_TYPES:
                    deprecated.append(f"{page.url} ({type_name})")
                for field_name in REQUIRED_FIELDS.get(type_name, []):
                    if not block.get(field_name):
                        missing_fields.append(f"{page.url} — {type_name} فاقد «{field_name}»")

    if broken:
        yield make_issue(
            "jsonld-invalid",
            "Invalid JSON-LD syntax",
            "کد JSON-LD خراب است",
            f"{len(broken)} بلوک JSON-LD قابل تجزیه نیست (معمولاً کاما یا کوتیشن اضافه). "
            "گوگل بلوک خراب را کاملاً نادیده می‌گیرد، یعنی انگار اصلاً داده ساختاریافته نداری.",
            "کد را با Rich Results Test گوگل یا validator.schema.org تست کن. اگر داده را "
            "به‌صورت رشته‌ای در قالب می‌سازی، حتماً مقادیر را json-encode کن نه دستی.",
            Severity.HIGH,
            Category.STRUCTURED_DATA,
            broken,
            sample(broken, 3),
            "https://search.google.com/test/rich-results",
        )

    if missing_fields:
        yield make_issue(
            "schema-missing-required",
            "Structured data missing required properties",
            "فیلدهای اجباری داده ساختاریافته پر نشده",
            f"{len(missing_fields)} مورد نشانه‌گذاری فیلد اجباری خود را ندارد. بدون فیلدهای "
            "اجباری، گوگل نشانه‌گذاری را نامعتبر می‌داند و نتیجه‌ی غنی نمایش نمی‌دهد.",
            "فیلدهای گزارش‌شده را کامل کن. فهرست دقیق فیلدهای هر نوع در مستندات نتایج غنی گوگل آمده است.",
            Severity.MEDIUM,
            Category.STRUCTURED_DATA,
            missing_fields,
            sample(missing_fields, 4),
            DOCS_SD,
        )

    if no_type:
        yield make_issue(
            "schema-no-type",
            "JSON-LD block without @type",
            "بلوک JSON-LD بدون @type",
            f"{len(no_type)} بلوک JSON-LD ویژگی @type ندارد و برای گوگل بی‌معناست.",
            "به هر بلوک JSON-LD یک @type معتبر از schema.org و همچنین @context برابر https://schema.org بده.",
            Severity.MEDIUM,
            Category.STRUCTURED_DATA,
            no_type,
            sample(no_type, 3),
            DOCS_SD,
        )

    if deprecated:
        reasons = "؛ ".join(f"{t}: {why}" for t, why in DEPRECATED_TYPES.items())
        yield make_issue(
            "schema-deprecated-type",
            "Structured data type no longer produces rich results",
            "نوع نشانه‌گذاری دیگر نتیجه‌ی غنی نمی‌دهد",
            f"{len(deprecated)} صفحه از نوعی استفاده می‌کند که گوگل دیگر برایش نتیجه‌ی غنی "
            f"نمایش نمی‌دهد. {reasons}. این نشانه‌گذاری ضرری ندارد ولی انتظار نتیجه‌ی غنی از آن نداشته باش.",
            "نگهش دار (برای درک معنایی و موتورهای دیگر مفید است) اما برای برد در نتایج، روی "
            "انواعی سرمایه‌گذاری کن که هنوز فعال‌اند: Product، Review، Breadcrumb، Article، "
            "Video، Event، JobPosting و LocalBusiness.",
            Severity.INFO,
            Category.STRUCTURED_DATA,
            deprecated,
            sample(deprecated, 3),
            DOCS_SD,
        )


@rule("structured-data-coverage")
def check_recommended_types(ctx: SiteContext) -> Iterator[Issue]:
    pages = ctx.indexable_pages
    if not pages:
        return

    present: set[str] = set()
    for page in pages:
        for block in page.jsonld:
            present.update(_types(block))
        present.update(page.microdata_types)

    if not present:
        return  # already reported by check_presence

    org_types = {"Organization", "LocalBusiness", "Corporation", "Person", "OnlineStore", "Store"}
    if not (present & org_types):
        yield make_issue(
            "no-organization-schema",
            "No Organization/LocalBusiness schema",
            "نشانه‌گذاری Organization یا LocalBusiness ندارد",
            "هیچ نشانه‌گذاری‌ای برای معرفی خود کسب‌وکار وجود ندارد. این نشانه‌گذاری هویت "
            "ناشر را برای گوگل تعریف می‌کند، پایه‌ی نمایش پنل دانش (Knowledge Panel) است و "
            "یکی از سیگنال‌های مستقیم اعتماد در ارزیابی E-E-A-T.",
            "در صفحه‌ی اصلی یک بلوک Organization بگذار با name، url، logo، sameAs (لینک "
            "شبکه‌های اجتماعی) و contactPoint. اگر کسب‌وکار محل فیزیکی دارد از LocalBusiness "
            "با address، telephone و openingHours استفاده کن.",
            Severity.MEDIUM,
            Category.EEAT,
            [ctx.start_url],
            f"انواع موجود: {'، '.join(sorted(present)[:8])}",
            DOCS_SD,
        )

    if "BreadcrumbList" not in present and len(pages) > 3:
        yield make_issue(
            "no-breadcrumb-schema",
            "No BreadcrumbList schema",
            "نشانه‌گذاری بردکرامب ندارد",
            "نشانه‌گذاری BreadcrumbList وجود ندارد. بردکرامب باعث می‌شود گوگل به‌جای آدرس خام، "
            "مسیر دسته‌بندی را در نتایج نشان بدهد و ساختار سایت را بهتر بفهمد.",
            "در صفحات داخلی BreadcrumbList با itemListElement مرتب‌شده اضافه کن و همزمان "
            "بردکرامب را به‌صورت بصری هم به کاربر نشان بده.",
            Severity.LOW,
            Category.STRUCTURED_DATA,
            [ctx.start_url],
            "",
            "https://developers.google.com/search/docs/appearance/structured-data/breadcrumb",
        )
