"""Orchestrates keyword research: expand → query the sources → score → cluster."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..config import KeywordConfig
from . import sources as source_module
from .analyze import Keyword, classify_intent, cluster, content_plan
from .expand import build_queries

ProgressFn = Callable[[str, int, int], None]


@dataclass
class KeywordReport:
    seed: str
    lang: str
    country: str
    keywords: list[Keyword] = field(default_factory=list)
    clusters: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    trending: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    queries_sent: int = 0
    ai_suggestions: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "lang": self.lang,
            "country": self.country,
            "total": len(self.keywords),
            "queries_sent": self.queries_sent,
            "keywords": [k.to_dict() for k in self.keywords],
            "clusters": self.clusters,
            "plan": self.plan,
            "questions": self.questions,
            "trending": self.trending,
            "entities": self.entities,
            "errors": self.errors,
            "by_intent": self.intent_breakdown(),
            "ai_suggestions": self.ai_suggestions,
        }

    def intent_breakdown(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for keyword in self.keywords:
            counts[keyword.intent] = counts.get(keyword.intent, 0) + 1
        return counts


def research(config: KeywordConfig, progress: ProgressFn | None = None) -> KeywordReport:
    report_progress = progress or (lambda message, done, total: None)

    def notify(message: str, done: int, total: int) -> None:
        try:
            report_progress(message, done, total)
        except Exception:
            pass

    seed = config.seed.strip()
    report = KeywordReport(seed=seed, lang=config.lang, country=config.country)
    if not seed:
        report.errors.append("عبارت اولیه خالی است.")
        return report

    queries = build_queries(
        seed,
        lang=config.lang,
        include_questions=config.include_questions,
        include_alphabet=config.include_alphabet,
        include_comparisons=config.include_comparisons,
    )
    active_sources = [s for s in config.sources if s in source_module.SOURCES]
    if not active_sources:
        active_sources = ["google"]

    jobs = [(query, name) for query in queries for name in active_sources]
    total_jobs = len(jobs)
    notify(f"ارسال {total_jobs} پرس‌وجو به {len(active_sources)} منبع", 0, total_jobs)

    collected: dict[str, Keyword] = {}
    failures: dict[str, int] = {}
    done = 0

    def run(job: tuple[str, str]) -> tuple[str, list]:
        query, name = job
        fn = source_module.SOURCES[name]
        try:
            if config.delay:
                time.sleep(config.delay)
            return name, fn(query, config.lang, config.country, config.timeout)
        except Exception as exc:  # network hiccups shouldn't abort the run
            return name, [exc]

    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, results in pool.map(run, jobs):
            done += 1
            if done % 15 == 0 or done == total_jobs:
                notify(f"{done} از {total_jobs} پرس‌وجو", done, total_jobs)
            if results and isinstance(results[0], Exception):
                failures[name] = failures.get(name, 0) + 1
                continue
            for suggestion in results:
                text = suggestion.keyword.strip()
                if not text or len(text) < 2:
                    continue
                existing = collected.get(text.lower())
                if existing is None:
                    existing = Keyword(keyword=text)
                    collected[text.lower()] = existing
                existing.sources.add(suggestion.source)
                existing.hits += 1
                existing.best_position = min(existing.best_position, suggestion.position)

    report.queries_sent = total_jobs

    for name, count in failures.items():
        report.errors.append(
            f"منبع «{name}» در {count} از {len(queries)} درخواست پاسخ نداد "
            "(احتمالاً محدودیت نرخ یا دسترسی شبکه)."
        )

    brand_terms = {t for t in seed.split() if len(t) > 3}
    for keyword in collected.values():
        keyword.intent = classify_intent(keyword.keyword, brand_terms)

    ranked = sorted(collected.values(), key=lambda k: (-k.demand_score, -k.hits, k.keyword))
    report.keywords = ranked[: config.max_keywords]

    from .expand import QUESTIONS_EN, QUESTIONS_FA

    question_markers = tuple(QUESTIONS_FA + QUESTIONS_EN) + ("؟", "?")
    report.questions = [
        k.keyword for k in report.keywords
        if any(marker in k.keyword.lower() for marker in question_markers)
    ][:40]

    notify("گرفتن ترندهای روز", total_jobs, total_jobs)
    report.trending = source_module.trending_now(config.country, config.timeout)
    report.entities = source_module.related_from_wikipedia(seed, config.lang, config.timeout)

    report.clusters = cluster(report.keywords, seed)
    report.plan = content_plan(report.clusters)

    if not report.keywords and not report.errors:
        report.errors.append(
            "هیچ پیشنهادی برگردانده نشد. عبارت اولیه را عمومی‌تر بنویس یا زبان/کشور را بررسی کن."
        )

    return report
