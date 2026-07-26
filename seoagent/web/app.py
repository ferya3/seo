"""Local Flask dashboard for the SEO agent."""

from __future__ import annotations

import json
from typing import Any

from flask import Flask, Response, jsonify, render_template, request

from .. import ai
from ..config import CrawlConfig, KeywordConfig, psi_api_key
from ..crawler import Crawler
from ..export import audit_markdown, keywords_markdown
from ..fetcher import normalize_url
from ..keywords.research import research
from ..report import build_report
from ..rules import run_all
from .jobs import Job, JobStore

store = JobStore()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    # ----------------------------------------------------------------- pages

    @app.route("/")
    def index() -> str:
        return render_template(
            "index.html",
            ai_enabled=ai.is_available(),
            jobs=[j.to_dict() for j in store.recent()],
        )

    @app.route("/report/<job_id>")
    def report_page(job_id: str):
        job = store.get(job_id)
        if job is None:
            return render_template("missing.html", job_id=job_id), 404
        if job.status != "done":
            return render_template("pending.html", job=job.to_dict()), 202
        template = "audit_report.html" if job.kind == "audit" else "keyword_report.html"
        return render_template(template, job=job.to_dict(), data=job.result)

    # ------------------------------------------------------------------- api

    @app.post("/api/audit")
    def start_audit():
        payload = request.get_json(silent=True) or {}
        url = (payload.get("url") or "").strip()
        if not url:
            return jsonify({"error": "آدرس سایت را وارد کن."}), 400
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            url = normalize_url(url)
        except Exception:
            return jsonify({"error": "آدرس واردشده معتبر نیست."}), 400

        keywords = [k.strip() for k in (payload.get("keywords") or "").split(",") if k.strip()]
        config = CrawlConfig(
            start_url=url,
            max_pages=_clamp(payload.get("max_pages"), 1, 300, 30),
            max_depth=_clamp(payload.get("max_depth"), 1, 8, 3),
            user_agent_key=payload.get("user_agent") or "mobile",
            respect_robots=bool(payload.get("respect_robots", True)),
            check_external_links=bool(payload.get("check_links", True)),
            follow_subdomains=bool(payload.get("subdomains", False)),
            target_keywords=keywords,
            include_psi=bool(payload.get("include_psi", False)),
            psi_api_key=psi_api_key(),
        )
        use_ai = bool(payload.get("use_ai", False)) and ai.is_available()

        job = store.create("audit", url)
        store.run(job, lambda j: _run_audit(j, config, use_ai))
        return jsonify(job.to_dict()), 202

    @app.post("/api/keywords")
    def start_keywords():
        payload = request.get_json(silent=True) or {}
        seed = (payload.get("seed") or "").strip()
        if not seed:
            return jsonify({"error": "موضوع یا عبارت اولیه را وارد کن."}), 400

        config = KeywordConfig(
            seed=seed,
            lang=(payload.get("lang") or "fa").strip(),
            country=(payload.get("country") or "IR").strip().upper(),
            max_keywords=_clamp(payload.get("max_keywords"), 20, 1000, 200),
            include_questions=bool(payload.get("questions", True)),
            include_alphabet=bool(payload.get("alphabet", True)),
            include_comparisons=bool(payload.get("comparisons", True)),
            sources=payload.get("sources") or ["google", "youtube", "bing", "duckduckgo"],
        )
        use_ai = bool(payload.get("use_ai", False)) and ai.is_available()

        job = store.create("keywords", seed)
        store.run(job, lambda j: _run_keywords(j, config, use_ai))
        return jsonify(job.to_dict()), 202

    @app.get("/api/job/<job_id>")
    def job_status(job_id: str):
        job = store.get(job_id)
        if job is None:
            return jsonify({"error": "چنین کاری پیدا نشد."}), 404
        return jsonify(job.to_dict())

    @app.get("/api/job/<job_id>/result")
    def job_result(job_id: str):
        job = store.get(job_id)
        if job is None:
            return jsonify({"error": "چنین کاری پیدا نشد."}), 404
        if job.status != "done":
            return jsonify({"error": "هنوز تمام نشده.", "status": job.status}), 409
        return Response(
            json.dumps(job.result, ensure_ascii=False, indent=2),
            mimetype="application/json; charset=utf-8",
        )

    @app.get("/api/job/<job_id>/export.md")
    def job_markdown(job_id: str):
        job = store.get(job_id)
        if job is None or job.status != "done" or job.result is None:
            return jsonify({"error": "گزارش آماده نیست."}), 404
        text = audit_markdown(job.result) if job.kind == "audit" else keywords_markdown(job.result)
        return Response(
            text,
            mimetype="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="seo-{job.kind}-{job.id}.md"'},
        )

    @app.get("/api/jobs")
    def list_jobs():
        return jsonify([j.to_dict() for j in store.recent()])

    return app


# --------------------------------------------------------------------- work


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _run_audit(job: Job, config: CrawlConfig, use_ai: bool) -> dict[str, Any]:
    crawler = Crawler(config, progress=JobStore.progress(job))
    ctx = crawler.crawl()

    if config.include_psi:
        job.message = "گرفتن داده‌ی Core Web Vitals از PageSpeed Insights"
        from .. import psi

        ctx.psi = psi.fetch(ctx.start_url, config.psi_api_key)

    job.message = "اجرای قوانین سئو"
    issues = run_all(ctx)
    report = build_report(ctx, issues)

    if use_ai:
        job.message = "گرفتن پیشنهادهای هوش مصنوعی"
        try:
            report.ai_suggestions = ai.audit_suggestions(ctx, issues, report.overall_score)
        except ai.AIUnavailable as exc:
            report.ai_suggestions = {"error": str(exc)}

    return report.to_dict()


def _run_keywords(job: Job, config: KeywordConfig, use_ai: bool) -> dict[str, Any]:
    report = research(config, progress=JobStore.progress(job))

    if use_ai and report.keywords:
        job.message = "گرفتن پیشنهادهای هوش مصنوعی"
        try:
            report.ai_suggestions = ai.keyword_suggestions(
                report.seed,
                [k.to_dict() for k in report.keywords],
                report.clusters,
                report.trending,
            )
        except ai.AIUnavailable as exc:
            report.ai_suggestions = {"error": str(exc)}

    return report.to_dict()
