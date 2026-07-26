"""Optimising for AI-driven search surfaces.

Google AI Overviews, ChatGPT Search, Perplexity and friends pick short,
self-contained passages that directly answer a question. That rewards a
different shape of content than classic blue-link SEO, so these checks look at
answer structure, crawler policy and machine-readable context.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from ..models import Category, Issue, Severity, SiteContext
from .base import make_issue, rule, sample

AI_CRAWLERS = [
    "gptbot", "chatgpt-user", "oai-searchbot", "claudebot", "anthropic-ai",
    "perplexitybot", "google-extended", "ccbot", "bytespider", "applebot-extended",
]

QUESTION_MARKERS = re.compile(
    r"(چیست|چگونه|چطور|چرا|کدام|چه\s|آیا|کجا|چند|\?|؟|"
    r"^(what|how|why|when|where|which|who|is|are|can|does|do)\b)",
    re.I,
)


@rule("ai-crawler-policy")
def check_ai_crawlers(ctx: SiteContext) -> Iterator[Issue]:
    if ctx.robots_txt is None:
        return

    lowered = ctx.robots_txt.lower()
    mentioned = [bot for bot in AI_CRAWLERS if bot in lowered]

    blocked: list[str] = []
    current: list[str] = []
    for raw in lowered.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("user-agent:"):
            current.append(line.split(":", 1)[1].strip())
        elif line.startswith("disallow:"):
            if line.split(":", 1)[1].strip() == "/":
                blocked.extend(a for a in current if a in AI_CRAWLERS)
        elif not line:
            current = []

    if blocked:
        yield make_issue(
            "ai-crawlers-blocked",
            "AI crawlers are blocked",
            "خزنده‌های هوش مصنوعی بلاک شده‌اند",
            f"در robots.txt این خزنده‌ها کاملاً بلاک شده‌اند: {'، '.join(sorted(set(blocked)))}. "
            "این یک تصمیم است نه لزوماً یک خطا: جلوی استفاده از محتوای تو برای آموزش مدل‌ها را "
            "می‌گیرد، اما در عوض سایتت در پاسخ‌های ChatGPT، Perplexity و ابزارهای مشابه هم "
            "دیده و ارجاع داده نمی‌شود.",
            "اگر هدفت دیده‌شدن است، خزنده‌های «جستجو» (OAI-SearchBot، PerplexityBot، ChatGPT-User) "
            "را باز بگذار و فقط خزنده‌های «آموزش مدل» (GPTBot، CCBot، Google-Extended) را ببند. "
            "توجه: بستن Google-Extended روی رتبه‌ی سایت در جستجوی معمولی گوگل اثری ندارد.",
            Severity.INFO,
            Category.AI_SEARCH,
            [ctx.start_url],
            "",
            "https://developers.google.com/search/docs/crawling-indexing/overview-google-crawlers",
        )
    elif not mentioned:
        yield make_issue(
            "ai-crawler-policy-undefined",
            "No explicit AI crawler policy",
            "سیاست مشخصی برای خزنده‌های هوش مصنوعی تعریف نشده",
            "در robots.txt هیچ اشاره‌ای به خزنده‌های هوش مصنوعی (GPTBot، ClaudeBot، PerplexityBot، "
            "Google-Extended و ...) نشده است. در حالت پیش‌فرض همه اجازه دارند. این لزوماً بد نیست، "
            "ولی بهتر است تصمیم آگاهانه باشد نه پیش‌فرض.",
            "تصمیم بگیر محتوایت برای آموزش مدل‌ها استفاده شود یا نه، و همان را صریح در robots.txt "
            "بنویس. اگر می‌خواهی در نتایج هوش مصنوعی دیده شوی، خزنده‌های جستجو را حتماً باز بگذار.",
            Severity.INFO,
            Category.AI_SEARCH,
            [ctx.start_url],
            "",
            "https://platform.openai.com/docs/bots",
        )


@rule("llms-txt")
def check_llms_txt(ctx: SiteContext) -> Iterator[Issue]:
    if ctx.llms_txt_found:
        return
    yield make_issue(
        "no-llms-txt",
        "No llms.txt",
        "فایل llms.txt وجود ندارد",
        "فایل /llms.txt وجود ندارد. این یک استاندارد نوظهور (هنوز رسمی و الزامی نیست) است که "
        "در قالب Markdown خلاصه‌ای از سایت و مهم‌ترین صفحاتش را برای مدل‌های زبانی فراهم می‌کند "
        "تا به‌جای تحلیل HTML شلوغ، مستقیم به محتوای اصلی برسند.",
        "یک فایل llms.txt در ریشه‌ی سایت بساز: یک تیتر H1 با نام سایت، یک پاراگراف توضیح، و "
        "بعد فهرست لینک‌دار مهم‌ترین صفحات با یک جمله توضیح برای هرکدام. هزینه‌اش کم است و "
        "اگر استاندارد جا بیفتد از قبل آماده‌ای.",
        Severity.INFO,
        Category.AI_SEARCH,
        [ctx.start_url],
        "",
        "https://llmstxt.org/",
    )


@rule("answer-structure")
def check_answer_structure(ctx: SiteContext) -> Iterator[Issue]:
    """AI answers are extracted from short passages, so the first paragraph
    under a question heading matters more than it used to."""
    pages = [p for p in ctx.indexable_pages if p.word_count >= 400]
    if not pages:
        return

    no_questions, no_lists, buried_answer = [], [], []

    for page in pages:
        headings = [text for level, text in page.headings if level in (2, 3)]
        if headings and not any(QUESTION_MARKERS.search(h) for h in headings):
            no_questions.append(page.url)

        if not re.search(r"<(ul|ol|table)\b", page.html, re.I):
            no_lists.append(page.url)

        # A very long first paragraph means no extractable snippet up top.
        first_paragraph = re.search(r"<p[^>]*>(.*?)</p>", page.html, re.I | re.S)
        if first_paragraph:
            words = len(re.sub(r"<[^>]+>", " ", first_paragraph.group(1)).split())
            if words > 120:
                buried_answer.append(f"{page.url} (پاراگراف اول {words} کلمه)")

    if no_questions:
        yield make_issue(
            "no-question-headings",
            "No question-style subheadings",
            "زیرتیترها به شکل سؤال نوشته نشده‌اند",
            f"{len(no_questions)} صفحه هیچ زیرتیتری به شکل سؤال ندارد. AI Overviews گوگل و "
            "اسنیپت‌های ویژه، پاسخ را از پاساژی برمی‌دارند که دقیقاً زیر یک سؤال آمده باشد. "
            "زیرتیتر سؤالی، صریح‌ترین راه برای گفتن «پاسخ این سؤال اینجاست».",
            "بخشی از زیرتیترها را به شکل سؤال واقعی کاربر بنویس (مثلاً «سئو تکنیکال چیست؟») و "
            "بلافاصله زیر آن، در دو تا سه جمله، پاسخ کامل و مستقل بده. جزئیات را بعد از آن بیاور.",
            Severity.MEDIUM,
            Category.AI_SEARCH,
            no_questions,
            sample(no_questions, 4),
            "https://developers.google.com/search/docs/appearance/featured-snippets",
        )

    if buried_answer:
        yield make_issue(
            "buried-answer",
            "Answer buried under a long intro",
            "پاسخ زیر مقدمه‌ی طولانی دفن شده",
            f"{len(buried_answer)} صفحه با پاراگراف اول بسیار طولانی شروع می‌شود. هم کاربر و هم "
            "سیستم‌های استخراج پاسخ، ابتدای صفحه را می‌خوانند؛ مقدمه‌ی طولانی یعنی پاسخ اصلی "
            "قابل استخراج نیست.",
            "از الگوی «اول پاسخ» استفاده کن: در ۴۰ تا ۶۰ کلمه‌ی اول مستقیماً جواب بده، بعد "
            "توضیح و زمینه را اضافه کن.",
            Severity.MEDIUM,
            Category.AI_SEARCH,
            buried_answer,
            sample(buried_answer, 3),
            "https://developers.google.com/search/docs/appearance/featured-snippets",
        )

    if no_lists and len(no_lists) > len(pages) * 0.6:
        yield make_issue(
            "no-lists-or-tables",
            "Content without lists or tables",
            "محتوا بدون فهرست یا جدول",
            f"{len(no_lists)} صفحه‌ی طولانی هیچ فهرست یا جدولی ندارد. اسنیپت‌های ویژه‌ی گوگل "
            "به‌شدت به سمت محتوای فهرستی و جدولی گرایش دارند، و مدل‌های زبانی هم داده‌ی "
            "ساخت‌یافته را راحت‌تر و دقیق‌تر نقل می‌کنند.",
            "مراحل را به فهرست شماره‌دار، ویژگی‌ها را به فهرست نقطه‌ای و مقایسه‌ها را به جدول تبدیل کن.",
            Severity.LOW,
            Category.AI_SEARCH,
            no_lists,
            sample(no_lists, 3),
            "https://developers.google.com/search/docs/appearance/featured-snippets",
        )


@rule("client-side-rendering")
def check_csr(ctx: SiteContext) -> Iterator[Issue]:
    """Content that only exists after JS runs is invisible to most AI crawlers,
    which — unlike Googlebot — usually do not execute JavaScript."""
    empty_shells = []
    for page in ctx.html_pages:
        if page.word_count < 50 and page.external_script_count >= 1 and page.byte_size > 2000:
            empty_shells.append(f"{page.url} ({page.word_count} کلمه در HTML اولیه)")

    if empty_shells:
        yield make_issue(
            "client-side-rendered",
            "Content rendered only by JavaScript",
            "محتوا فقط با جاوااسکریپت رندر می‌شود",
            f"{len(empty_shells)} صفحه در HTML اولیه تقریباً هیچ متنی ندارد و محتوا بعداً با "
            "جاوااسکریپت ساخته می‌شود. گوگل جاوااسکریپت را اجرا می‌کند اما با تأخیر و در صف؛ "
            "در مقابل، خزنده‌های ChatGPT و Perplexity معمولاً جاوااسکریپت اجرا نمی‌کنند و "
            "صفحه‌ی تو را خالی می‌بینند.",
            "رندر سمت سرور (SSR) یا تولید ایستا (SSG) را فعال کن تا محتوای اصلی در همان HTML "
            "اولیه باشد. در Next.js و Nuxt این حالت پیش‌فرض است؛ برای اپلیکیشن‌های React خالص "
            "می‌توانی از prerendering استفاده کنی.",
            Severity.HIGH,
            Category.AI_SEARCH,
            empty_shells,
            sample(empty_shells, 3),
            "https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics",
        )
