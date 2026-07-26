"""Scoring, search-intent classification and topic clustering for keywords."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .expand import (
    COMMERCIAL_EN,
    COMMERCIAL_FA,
    INFORMATIONAL_EN,
    INFORMATIONAL_FA,
    LOCAL_EN,
    LOCAL_FA,
    QUESTIONS_EN,
    QUESTIONS_FA,
)

TRANSACTIONAL = {
    "خرید", "سفارش", "ثبت نام", "ثبت‌نام", "دانلود", "رزرو", "استعلام", "پرداخت",
    "buy", "order", "download", "signup", "sign up", "book", "subscribe", "hire",
}
COMMERCIAL = set(COMMERCIAL_FA) | set(COMMERCIAL_EN) | {"مقایسه", "بررسی", "نقد", "review", "vs", "بهترین"}
INFORMATIONAL = set(INFORMATIONAL_FA) | set(INFORMATIONAL_EN) | set(QUESTIONS_FA) | set(QUESTIONS_EN)
LOCAL = set(LOCAL_FA) | set(LOCAL_EN) | {"نزدیک من", "near me", "تهران", "شیراز", "مشهد", "اصفهان", "کرج", "تبریز"}

INTENT_FA = {
    "transactional": "خرید / اقدام",
    "commercial": "بررسی قبل از خرید",
    "informational": "اطلاعاتی",
    "local": "محلی",
    "navigational": "برند / ناوبری",
}

_STOPWORDS_FA = {
    "و", "در", "به", "از", "که", "را", "با", "برای", "این", "آن", "است", "می",
    "یک", "تا", "بر", "هم", "یا", "چه", "های", "ها", "شده", "کرد", "کردن",
}
_STOPWORDS_EN = {
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "is", "are",
    "with", "how", "what", "why", "best", "vs",
}
STOPWORDS = _STOPWORDS_FA | _STOPWORDS_EN

_TOKEN_RE = re.compile(r"[\w؀-ۿ]+")


@dataclass
class Keyword:
    keyword: str
    sources: set[str] = field(default_factory=set)
    hits: int = 0                # how many probe queries surfaced it
    best_position: int = 99      # best rank inside any autocomplete dropdown
    intent: str = "informational"

    @property
    def word_count(self) -> int:
        return len(_TOKEN_RE.findall(self.keyword))

    @property
    def is_long_tail(self) -> bool:
        return self.word_count >= 4

    @property
    def demand_score(self) -> int:
        """Relative demand proxy, 0-100.

        Autocomplete does not expose volume, so this combines the signals that
        do correlate with it: how many independent engines suggest the phrase,
        how often it resurfaces across different probes, and how high it ranks
        in the dropdown (engines order by popularity).
        """
        source_signal = min(len(self.sources), 4) / 4 * 40
        frequency_signal = min(self.hits, 10) / 10 * 35
        position_signal = max(0.0, (10 - min(self.best_position, 10)) / 10) * 25
        return round(source_signal + frequency_signal + position_signal)

    @property
    def opportunity_score(self) -> int:
        """Demand weighed against how hard the phrase is likely to be.

        Short head terms are the most contested, so a long-tail phrase with
        decent demand is usually the better first target for a personal site.
        """
        length_bonus = min(self.word_count, 6) / 6 * 30
        head_penalty = 15 if self.word_count <= 2 else 0
        return max(0, min(100, round(self.demand_score * 0.7 + length_bonus - head_penalty)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "sources": sorted(self.sources),
            "hits": self.hits,
            "position": self.best_position,
            "words": self.word_count,
            "long_tail": self.is_long_tail,
            "intent": self.intent,
            "intent_fa": INTENT_FA.get(self.intent, self.intent),
            "demand": self.demand_score,
            "opportunity": self.opportunity_score,
        }


def classify_intent(keyword: str, brand_terms: set[str] | None = None) -> str:
    lowered = keyword.lower()
    tokens = set(_TOKEN_RE.findall(lowered))

    if brand_terms and tokens & {b.lower() for b in brand_terms}:
        return "navigational"
    if any(term in lowered for term in LOCAL):
        return "local"
    if tokens & TRANSACTIONAL or any(term in lowered for term in TRANSACTIONAL):
        return "transactional"
    if tokens & COMMERCIAL or any(term in lowered for term in COMMERCIAL):
        return "commercial"
    if tokens & INFORMATIONAL or any(term in lowered for term in INFORMATIONAL):
        return "informational"
    return "informational"


def tokens_of(keyword: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(keyword.lower()) if t not in STOPWORDS and len(t) > 1]


def cluster(keywords: list[Keyword], seed: str, max_clusters: int = 12) -> list[dict[str, Any]]:
    """Group keywords into topic clusters by their most distinctive shared token.

    Each cluster maps naturally onto one page: a cluster is a page's worth of
    intent, and the highest-demand member is its target keyword.
    """
    seed_tokens = set(tokens_of(seed))
    buckets: dict[str, list[Keyword]] = defaultdict(list)

    # Global token frequency, so we can pick a distinctive label rather than a
    # token that appears in every phrase.
    frequency: dict[str, int] = defaultdict(int)
    for keyword in keywords:
        for token in set(tokens_of(keyword.keyword)) - seed_tokens:
            frequency[token] += 1

    for keyword in keywords:
        candidates = [t for t in tokens_of(keyword.keyword) if t not in seed_tokens]
        if not candidates:
            buckets[seed].append(keyword)
            continue
        # Prefer a token that groups a meaningful number of siblings but isn't universal.
        candidates.sort(key=lambda t: (-frequency[t], len(t)))
        label = next((t for t in candidates if 2 <= frequency[t] <= len(keywords) * 0.6), candidates[0])
        buckets[label].append(keyword)

    clusters = []
    for label, members in buckets.items():
        members.sort(key=lambda k: -k.demand_score)
        intents = defaultdict(int)
        for member in members:
            intents[member.intent] += 1
        dominant = max(intents.items(), key=lambda kv: kv[1])[0]
        clusters.append(
            {
                "label": label,
                "size": len(members),
                "intent": dominant,
                "intent_fa": INTENT_FA.get(dominant, dominant),
                "avg_demand": round(sum(m.demand_score for m in members) / len(members)),
                "primary": members[0].keyword,
                "keywords": [m.to_dict() for m in members[:25]],
            }
        )

    clusters.sort(key=lambda c: (-c["size"], -c["avg_demand"]))
    return clusters[:max_clusters]


def content_plan(clusters: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Translate clusters into concrete page recommendations."""
    page_type = {
        "transactional": ("صفحه‌ی محصول یا خدمت", "قیمت، مشخصات، فراخوان خرید و نشانه‌گذاری Product"),
        "commercial": ("مقاله‌ی مقایسه‌ای یا بررسی", "جدول مقایسه، مزایا و معایب، تجربه‌ی واقعی استفاده"),
        "informational": ("مقاله‌ی آموزشی جامع", "پاسخ کوتاه در ابتدا، سپس مراحل، مثال و پرسش‌های متداول"),
        "local": ("صفحه‌ی محلی", "آدرس، نقشه، ساعت کاری و نشانه‌گذاری LocalBusiness"),
        "navigational": ("صفحه‌ی برند", "معرفی، تماس و لینک به صفحات اصلی"),
    }

    plan = []
    for item in clusters[:limit]:
        kind, elements = page_type.get(item["intent"], page_type["informational"])
        supporting = [k["keyword"] for k in item["keywords"][1:8]]
        plan.append(
            {
                "primary_keyword": item["primary"],
                "page_type": kind,
                "must_include": elements,
                "supporting_keywords": supporting,
                "cluster_size": item["size"],
                "intent_fa": item["intent_fa"],
                "suggested_title": _suggest_title(item["primary"], item["intent"]),
            }
        )
    return plan


def _suggest_title(keyword: str, intent: str) -> str:
    patterns = {
        "informational": f"{keyword}: راهنمای کامل و کاربردی",
        "commercial": f"بهترین {keyword} — مقایسه و راهنمای انتخاب",
        "transactional": f"{keyword} با بهترین قیمت و ارسال سریع",
        "local": f"{keyword} — نزدیک شما",
        "navigational": keyword,
    }
    return patterns.get(intent, patterns["informational"])
