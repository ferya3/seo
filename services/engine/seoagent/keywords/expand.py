"""Query expansion patterns.

Autocomplete only answers what you ask it, so the trick is asking many shaped
variations of the seed. These modifier lists are what turn one seed into a few
hundred real, currently-suggested queries.
"""

from __future__ import annotations

PERSIAN_ALPHABET = list("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی")
LATIN_ALPHABET = list("abcdefghijklmnopqrstuvwxyz")

QUESTIONS_FA = [
    "چیست", "چگونه", "چطور", "چرا", "کدام", "کجا", "چند", "آیا",
    "یعنی چه", "چه زمانی", "چه کسی", "چقدر",
]
QUESTIONS_EN = [
    "what is", "how to", "why", "when", "where", "which", "who", "can",
    "is", "does", "vs", "how much", "how many",
]

COMMERCIAL_FA = [
    "قیمت", "خرید", "بهترین", "ارزان", "تخفیف", "فروش", "سفارش", "هزینه",
    "نمایندگی", "اقساطی", "دست دوم", "اورجینال", "تعرفه",
]
COMMERCIAL_EN = [
    "price", "buy", "best", "cheap", "discount", "cost", "review", "top",
    "for sale", "deals", "pricing", "alternative",
]

INFORMATIONAL_FA = [
    "آموزش", "راهنما", "معرفی", "مقایسه", "تفاوت", "مزایا", "معایب", "کاربرد",
    "انواع", "نمونه", "مثال", "روش", "مراحل", "اصول", "نکات", "ترفند",
    "رایگان", "آنلاین", "pdf", "دانلود", "شرایط", "قوانین",
]
INFORMATIONAL_EN = [
    "tutorial", "guide", "examples", "template", "checklist", "tips",
    "free", "online", "pdf", "course", "meaning", "types", "benefits",
]

LOCAL_FA = ["در تهران", "نزدیک من", "در ایران", "در مشهد", "در اصفهان", "در شیراز"]
LOCAL_EN = ["near me", "in tehran", "online"]

COMPARISON_FA = ["یا", "در مقابل", "بهتر است یا", "و"]
COMPARISON_EN = ["vs", "or", "alternative to", "compared to"]

TIME_FA = ["۱۴۰۵", "2026", "جدید", "امسال", "به روز"]
TIME_EN = ["2026", "latest", "new", "updated"]


def _bucket(lang: str) -> dict[str, list[str]]:
    persian = lang.lower().startswith("fa") or lang.lower().startswith("ar")
    if persian:
        return {
            "alphabet": PERSIAN_ALPHABET,
            "questions": QUESTIONS_FA,
            "commercial": COMMERCIAL_FA,
            "informational": INFORMATIONAL_FA,
            "local": LOCAL_FA,
            "comparison": COMPARISON_FA,
            "time": TIME_FA,
        }
    return {
        "alphabet": LATIN_ALPHABET,
        "questions": QUESTIONS_EN,
        "commercial": COMMERCIAL_EN,
        "informational": INFORMATIONAL_EN,
        "local": LOCAL_EN,
        "comparison": COMPARISON_EN,
        "time": TIME_EN,
    }


def build_queries(
    seed: str,
    lang: str = "fa",
    include_questions: bool = True,
    include_alphabet: bool = True,
    include_comparisons: bool = True,
) -> list[str]:
    """Return the list of probe queries to send to the autocomplete sources."""
    seed = seed.strip()
    if not seed:
        return []

    buckets = _bucket(lang)
    queries: list[str] = [seed]

    if include_questions:
        # Persian questions usually trail the noun ("سئو چیست"), English lead it.
        for word in buckets["questions"]:
            if lang.lower().startswith("fa"):
                queries.append(f"{seed} {word}")
            else:
                queries.append(f"{word} {seed}")

    for word in buckets["commercial"] + buckets["informational"]:
        queries.append(f"{seed} {word}")
        queries.append(f"{word} {seed}")

    for word in buckets["local"] + buckets["time"]:
        queries.append(f"{seed} {word}")

    if include_comparisons:
        for word in buckets["comparison"]:
            queries.append(f"{seed} {word}")

    if include_alphabet:
        for letter in buckets["alphabet"]:
            queries.append(f"{seed} {letter}")

    # De-duplicate, preserve order.
    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))
