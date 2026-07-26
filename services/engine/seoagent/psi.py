"""Optional PageSpeed Insights lookup for real Core Web Vitals field data.

The public endpoint works without a key at low volume; a key (PAGESPEED_API_KEY)
raises the quota. When the call fails we degrade silently — the rule engine
falls back to its own static heuristics.
"""

from __future__ import annotations

from typing import Any

import requests

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# Google's "good" thresholds. INP replaced FID as a Core Web Vital in March 2024.
THRESHOLDS = {
    "LARGEST_CONTENTFUL_PAINT_MS": (2500, 4000, "LCP", "بارگذاری بزرگ‌ترین محتوا", "میلی‌ثانیه"),
    "INTERACTION_TO_NEXT_PAINT": (200, 500, "INP", "پاسخ‌دهی به تعامل کاربر", "میلی‌ثانیه"),
    "CUMULATIVE_LAYOUT_SHIFT_SCORE": (0.1, 0.25, "CLS", "جابه‌جایی ناگهانی چیدمان", ""),
    "FIRST_CONTENTFUL_PAINT_MS": (1800, 3000, "FCP", "نمایش اولین محتوا", "میلی‌ثانیه"),
    "EXPERIMENTAL_TIME_TO_FIRST_BYTE": (800, 1800, "TTFB", "زمان تا اولین بایت", "میلی‌ثانیه"),
}


def fetch(url: str, api_key: str | None = None, strategy: str = "mobile", timeout: float = 60.0) -> dict[str, Any] | None:
    params = {"url": url, "strategy": strategy, "category": "performance"}
    if api_key:
        params["key"] = api_key
    try:
        response = requests.get(ENDPOINT, params=params, timeout=timeout)
        if response.status_code != 200:
            return {"error": f"PageSpeed Insights کد {response.status_code} برگرداند"}
        return _summarise(response.json(), strategy)
    except requests.RequestException as exc:
        return {"error": f"دسترسی به PageSpeed Insights ممکن نشد: {type(exc).__name__}"}
    except ValueError:
        return {"error": "پاسخ PageSpeed Insights قابل خواندن نبود"}


def _summarise(payload: dict[str, Any], strategy: str) -> dict[str, Any]:
    result: dict[str, Any] = {"strategy": strategy, "field": {}, "lab": {}, "opportunities": []}

    loading = payload.get("loadingExperience") or {}
    origin_loading = payload.get("originLoadingExperience") or {}
    result["has_field_data"] = bool(loading.get("metrics"))

    for source_key, bucket in (("metrics", "field"), ):
        for metric, values in (loading.get(source_key) or {}).items():
            if metric not in THRESHOLDS:
                continue
            good, poor, short, label_fa, unit = THRESHOLDS[metric]
            percentile = values.get("percentile")
            if percentile is None:
                continue
            value = percentile / 100 if metric == "CUMULATIVE_LAYOUT_SHIFT_SCORE" else percentile
            result[bucket][short] = {
                "value": value,
                "unit": unit,
                "label_fa": label_fa,
                "category": values.get("category", "NONE"),
                "good_threshold": good,
                "poor_threshold": poor,
                "status": "good" if value <= good else ("needs-improvement" if value <= poor else "poor"),
            }

    if origin_loading.get("overall_category"):
        result["origin_category"] = origin_loading["overall_category"]

    lighthouse = payload.get("lighthouseResult") or {}
    audits = lighthouse.get("audits") or {}
    categories = lighthouse.get("categories") or {}
    perf = categories.get("performance") or {}
    if perf.get("score") is not None:
        result["lab_score"] = round(perf["score"] * 100)

    for audit_id in ("largest-contentful-paint", "cumulative-layout-shift", "total-blocking-time",
                     "speed-index", "first-contentful-paint", "server-response-time"):
        audit = audits.get(audit_id)
        if audit and audit.get("displayValue"):
            result["lab"][audit_id] = {
                "title": audit.get("title", audit_id),
                "display": audit["displayValue"],
                "score": audit.get("score"),
            }

    for audit_id, audit in audits.items():
        details = audit.get("details") or {}
        savings = details.get("overallSavingsMs")
        if savings and savings >= 150 and (audit.get("score") is None or audit["score"] < 0.9):
            result["opportunities"].append(
                {"id": audit_id, "title": audit.get("title", audit_id), "savings_ms": int(savings)}
            )
    result["opportunities"].sort(key=lambda o: -o["savings_ms"])
    result["opportunities"] = result["opportunities"][:8]

    return result
