"""hreflang and multi-language correctness."""

from __future__ import annotations

import re
from collections.abc import Iterator

from ..fetcher import normalize_url
from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

DOCS_HREFLANG = "https://developers.google.com/search/docs/specialty/international/localized-versions"

# ISO 639-1 language, optionally a script, optionally an ISO 3166-1 region.
HREFLANG_RE = re.compile(r"^([a-z]{2,3})(-[A-Za-z]{4})?(-[A-Za-z]{2}|-\d{3})?$")


@rule("hreflang")
def check_hreflang(ctx: SiteContext) -> Iterator[Issue]:
    pages_with = [p for p in ctx.html_pages if p.hreflang]
    if not pages_with:
        return

    invalid, no_self, no_default, unreciprocated = [], [], [], []
    by_url = {normalize_url(p.final_url or p.url): p for p in ctx.html_pages}

    for page in pages_with:
        codes = [code for code, _ in page.hreflang]
        current = normalize_url(page.final_url or page.url)

        for code, _href in page.hreflang:
            if code.lower() != "x-default" and not HREFLANG_RE.match(code):
                invalid.append(f"{page.url} → کد «{code}»")

        targets = {normalize_url(href) for _, href in page.hreflang}
        if current not in targets:
            no_self.append(page.url)

        if not any(c.lower() == "x-default" for c in codes):
            no_default.append(page.url)

        # Every alternate must point back, or Google ignores the whole cluster.
        for _, href in page.hreflang:
            target = by_url.get(normalize_url(href))
            if target is None or not target.hreflang:
                continue
            back = {normalize_url(h) for _, h in target.hreflang}
            if current not in back:
                unreciprocated.append(f"{page.url} ↔ {href}")

    if invalid:
        yield make_issue(
            "hreflang-invalid-code",
            "Invalid hreflang codes",
            "کد hreflang نامعتبر است",
            f"{len(invalid)} مقدار hreflang فرمت درستی ندارد. فرمت درست کد زبان ISO 639-1 است "
            "که می‌تواند با کد کشور ISO 3166-1 ترکیب شود — مثل fa، fa-IR یا en-US. "
            "نکته‌ی رایج: «fa-FA» یا «en-UK» غلط است (درستشان fa-IR و en-GB است).",
            "کدها را با استاندارد تطبیق بده. کد زبان همیشه اول می‌آید و کد کشور به‌تنهایی مجاز نیست.",
            Severity.HIGH,
            Category.INTERNATIONAL,
            invalid,
            sample(invalid, 4),
            DOCS_HREFLANG,
        )

    if no_self:
        yield make_issue(
            "hreflang-no-self",
            "hreflang missing self-reference",
            "hreflang به خودش اشاره نمی‌کند",
            f"{len(no_self)} صفحه در فهرست hreflang خود، خودش را ذکر نکرده است. گوگل صراحتاً "
            "می‌گوید هر صفحه باید یک hreflang خودارجاع داشته باشد، وگرنه کل مجموعه نادیده گرفته می‌شود.",
            "به فهرست hreflang هر صفحه، خود آن صفحه را با کد زبان خودش اضافه کن.",
            Severity.HIGH,
            Category.INTERNATIONAL,
            no_self,
            sample(no_self, 4),
            DOCS_HREFLANG,
        )

    if unreciprocated:
        yield make_issue(
            "hreflang-not-reciprocal",
            "hreflang links are not reciprocal",
            "لینک‌های hreflang دوطرفه نیستند",
            f"{len(unreciprocated)} رابطه‌ی hreflang یک‌طرفه است: صفحه‌ی A به B اشاره می‌کند ولی "
            "B به A برنمی‌گردد. گوگل رابطه‌های یک‌طرفه را کاملاً نادیده می‌گیرد.",
            "همه‌ی نسخه‌های زبانی باید فهرست hreflang یکسان و کاملی داشته باشند که همه‌ی "
            "نسخه‌ها از جمله خودشان را پوشش بدهد.",
            Severity.HIGH,
            Category.INTERNATIONAL,
            unreciprocated,
            sample(unreciprocated, 4),
            DOCS_HREFLANG,
        )

    if no_default and len(no_default) == len(pages_with):
        yield make_issue(
            "hreflang-no-x-default",
            "No x-default hreflang",
            "مقدار x-default تعریف نشده",
            "هیچ صفحه‌ای hreflang با مقدار x-default ندارد. این مقدار به گوگل می‌گوید برای "
            "کاربرانی که هیچ‌کدام از زبان‌های تعریف‌شده با آن‌ها مطابقت ندارد، کدام نسخه را نشان بدهد.",
            "یک ورودی `<link rel=\"alternate\" hreflang=\"x-default\" href=\"...\">` اضافه کن که "
            "معمولاً به نسخه‌ی انگلیسی یا به صفحه‌ی انتخاب زبان اشاره می‌کند.",
            Severity.MEDIUM,
            Category.INTERNATIONAL,
            no_default,
            sample(no_default, 3),
            DOCS_HREFLANG,
        )


@rule("rtl-direction")
def check_rtl(ctx: SiteContext) -> Iterator[Issue]:
    """Persian/Arabic content needs dir=rtl, and it is easy to forget."""
    offenders = []
    persian_chars = re.compile(r"[؀-ۿ]")

    for page in ctx.html_pages:
        if not persian_chars.search(page.text[:2000]):
            continue
        html_head = page.html[:3000].lower()
        if 'dir="rtl"' in html_head or "dir='rtl'" in html_head or "direction:rtl" in html_head.replace(" ", ""):
            continue
        offenders.append(page.url)

    if offenders:
        yield make_issue(
            "missing-rtl-direction",
            "Persian content without dir=rtl",
            "محتوای فارسی بدون تنظیم جهت راست‌به‌چپ",
            f"{len(offenders)} صفحه محتوای فارسی دارد ولی روی تگ <html> ویژگی dir=\"rtl\" ندارد. "
            "بدون آن، علائم نگارشی و اعداد و متن انگلیسی داخل جمله جابه‌جا نمایش داده می‌شوند "
            "که هم خوانایی را خراب می‌کند و هم نرخ پرش را بالا می‌برد.",
            'روی تگ html بنویس `<html lang="fa" dir="rtl">`. برای بلوک‌های کد یا متن انگلیسی '
            'داخل صفحه می‌توانی به‌صورت موضعی `dir="ltr"` بگذاری.',
            Severity.MEDIUM,
            Category.INTERNATIONAL,
            offenders,
            sample(offenders, 4),
            DOCS_HREFLANG,
        )
