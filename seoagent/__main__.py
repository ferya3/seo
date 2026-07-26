"""Entry point.

    python -m seoagent                      launch the dashboard
    python -m seoagent audit <url>          run an audit in the terminal
    python -m seoagent keywords "<topic>"   run keyword research in the terminal
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from threading import Timer


def _serve(args: argparse.Namespace) -> int:
    from .web import create_app

    app = create_app()
    url = f"http://{args.host}:{args.port}"
    print(f"\n  ایجنت سئو روی {url} بالا آمد")
    print("  برای خاموش کردن Ctrl+C را بزن\n")
    if args.open:
        Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=args.debug)
    return 0


def _cli_progress(message: str, done: int, total: int) -> None:
    suffix = f" ({done}/{total})" if total else ""
    sys.stderr.write(f"\r\033[K  {message}{suffix}")
    sys.stderr.flush()


def _audit(args: argparse.Namespace) -> int:
    from .config import CrawlConfig, psi_api_key
    from .crawler import Crawler
    from .export import audit_markdown
    from .report import build_report
    from .rules import run_all

    url = args.url if args.url.startswith(("http://", "https://")) else "https://" + args.url
    config = CrawlConfig(
        start_url=url,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        user_agent_key=args.user_agent,
        check_external_links=not args.skip_links,
        target_keywords=[k.strip() for k in (args.keywords or "").split(",") if k.strip()],
        include_psi=args.psi,
        psi_api_key=psi_api_key(),
    )

    ctx = Crawler(config, progress=_cli_progress).crawl()
    if args.psi:
        from . import psi as psi_module

        ctx.psi = psi_module.fetch(ctx.start_url, config.psi_api_key)

    report = build_report(ctx, run_all(ctx))

    if args.ai:
        from . import ai

        try:
            report.ai_suggestions = ai.audit_suggestions(ctx, report.issues, report.overall_score)
        except ai.AIUnavailable as exc:
            report.ai_suggestions = {"error": str(exc)}

    sys.stderr.write("\r\033[K")
    payload = report.to_dict()
    output = json.dumps(payload, ensure_ascii=False, indent=2) if args.json else audit_markdown(payload)
    _write(output, args.out)
    return 0


def _keywords(args: argparse.Namespace) -> int:
    from .config import KeywordConfig
    from .export import keywords_markdown
    from .keywords.research import research

    config = KeywordConfig(
        seed=args.seed,
        lang=args.lang,
        country=args.country,
        max_keywords=args.limit,
        include_alphabet=not args.fast,
    )
    report = research(config, progress=_cli_progress)

    if args.ai and report.keywords:
        from . import ai

        try:
            report.ai_suggestions = ai.keyword_suggestions(
                report.seed,
                [k.to_dict() for k in report.keywords],
                report.clusters,
                report.trending,
            )
        except ai.AIUnavailable as exc:
            report.ai_suggestions = {"error": str(exc)}

    sys.stderr.write("\r\033[K")
    payload = report.to_dict()
    output = json.dumps(payload, ensure_ascii=False, indent=2) if args.json else keywords_markdown(payload)
    _write(output, args.out)
    return 0


def _write(text: str, path: str | None) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"در {path} نوشته شد")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seoagent", description="ایجنت شخصی سئو")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="اجرای داشبورد وب (پیش‌فرض)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--debug", action="store_true")
    serve.add_argument("--no-open", dest="open", action="store_false", help="مرورگر را باز نکن")
    serve.set_defaults(func=_serve, open=True)

    audit = subparsers.add_parser("audit", help="بررسی سئوی یک سایت")
    audit.add_argument("url")
    audit.add_argument("--max-pages", type=int, default=30)
    audit.add_argument("--max-depth", type=int, default=3)
    audit.add_argument("--user-agent", default="mobile", choices=["mobile", "desktop", "googlebot"])
    audit.add_argument("--keywords", help="کلمات کلیدی هدف، با ویرگول")
    audit.add_argument("--skip-links", action="store_true", help="لینک‌های خارجی را بررسی نکن")
    audit.add_argument("--psi", action="store_true", help="داده‌ی Core Web Vitals از PageSpeed Insights")
    audit.add_argument("--ai", action="store_true", help="پیشنهادهای Claude (نیازمند ANTHROPIC_API_KEY)")
    audit.add_argument("--json", action="store_true", help="خروجی JSON به‌جای Markdown")
    audit.add_argument("-o", "--out", help="نوشتن در فایل")
    audit.set_defaults(func=_audit)

    keywords = subparsers.add_parser("keywords", help="تحقیق کلمات کلیدی")
    keywords.add_argument("seed")
    keywords.add_argument("--lang", default="fa")
    keywords.add_argument("--country", default="IR")
    keywords.add_argument("--limit", type=int, default=200)
    keywords.add_argument("--fast", action="store_true", help="بدون پویش الفبایی (سریع‌تر)")
    keywords.add_argument("--ai", action="store_true", help="پیشنهادهای Claude (نیازمند ANTHROPIC_API_KEY)")
    keywords.add_argument("--json", action="store_true", help="خروجی JSON به‌جای Markdown")
    keywords.add_argument("-o", "--out", help="نوشتن در فایل")
    keywords.set_defaults(func=_keywords)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve", *(argv or [])])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
