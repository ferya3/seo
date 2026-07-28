"""The first thing a user can send to someone else.

Six services produce findings and the orchestrator gathers them; until now the
only way to read the result was a JSON payload or the dashboard. This turns a
finished workflow into one document — Markdown for a repository or a chat, and
a self-contained HTML page that prints to PDF.

Self-contained is not a nicety. A report that pulls a stylesheet from a CDN is
a report that looks broken when it is opened offline, six months later, by
someone who was emailed it — which is exactly how a report gets read.

Pure functions of the workflow report. No network, no database, no clock
beyond the timestamp handed in.
"""

from __future__ import annotations

import html
from typing import Any

STATUS_FA = {
    "completed": "انجام شد",
    "failed": "شکست خورد",
    "skipped": "انجام نشد",
    "running": "در حال اجرا",
    "queued": "در صف",
    "pending": "در انتظار",
    "dispatched": "در حال اجرا",
}

STEP_FA = {
    "crawl": "خزش سایت",
    "keyword_research": "تحقیق کلمات کلیدی",
    "serp_check": "بررسی جایگاه",
    "link_analysis": "تحلیل لینک داخلی",
    "content_analysis": "پوشش محتوا",
    "optimizer_plan": "پیشنهاد بازنویسی",
    "competitor_crawl": "خزش سایت رقیب",
    "competitor_check": "مقایسه با رقبا",
}

HEADLINE_FA = [
    ("overall_score", "امتیاز کلی"),
    ("total_issues", "تعداد ایرادها"),
    ("keywords_found", "کلمات کلیدی"),
    ("keywords_ranked", "رتبه‌گرفته"),
    ("average_position", "میانگین جایگاه"),
    ("orphan_pages", "صفحه‌ی یتیم"),
    ("keyword_coverage", "پوشش کلمات (٪)"),
    ("pages_to_rewrite", "صفحه برای بازنویسی"),
    ("competitors_compared", "رقیب مقایسه‌شده"),
    ("behind_on", "سنجه‌ای که عقب‌اید"),
]


def title_of(workflow: dict[str, Any]) -> str:
    site = ((workflow.get("inputs") or {}).get("start_url")
            or (workflow.get("report") or {}).get("crawl", {}).get("start_url")
            or "")
    return f"گزارش سئو — {site}" if site else "گزارش سئو"


# ------------------------------------------------------------------- markdown


def markdown(workflow: dict[str, Any]) -> str:
    report = workflow.get("report") or {}
    summary = report.get("summary") or {}
    lines: list[str] = [f"# {title_of(workflow)}", ""]

    lines += [
        f"**وضعیت:** {STATUS_FA.get(workflow.get('status'), workflow.get('status', '—'))}  ",
        f"**تاریخ:** {workflow.get('created_at') or '—'}  ",
        f"**شناسه:** `{workflow.get('workflow_id', '—')}`",
        "",
    ]
    if workflow.get("error"):
        lines += [f"> این تحلیل کامل نشد: {workflow['error']}", ""]

    if summary.get("text_fa"):
        source = "نوشته‌ی مدل" if summary.get("source") == "ai" else "قانون‌محور"
        lines += [f"## خلاصه ({source})", "", summary["text_fa"], ""]

        if summary.get("next_actions"):
            lines += ["### قدم‌های بعدی", ""]
            for index, action in enumerate(summary["next_actions"], start=1):
                lines.append(f"{index}. **{action.get('action', '')}**  ")
                lines.append(
                    f"   {action.get('why', '')} — تلاش: {action.get('effort', '—')}، "
                    f"اثر: {action.get('impact', '—')}"
                )
            lines.append("")

        if summary.get("watch_outs"):
            lines += ["### چیزهایی که این گزارش نمی‌داند", ""]
            lines += [f"- {item}" for item in summary["watch_outs"]] + [""]

    numbers = _headline_rows(report)
    if numbers:
        lines += ["## اعداد اصلی", "", "| معیار | مقدار |", "| --- | --- |"]
        lines += [f"| {label} | {value} |" for label, value in numbers] + [""]

    steps = workflow.get("steps") or []
    if steps:
        lines += ["## مراحل", "", "| # | مرحله | وضعیت | توضیح |", "| --- | --- | --- | --- |"]
        for step in sorted(steps, key=lambda s: s.get("position", 0)):
            lines.append(
                f"| {step.get('position', '')} "
                f"| {STEP_FA.get(step.get('kind'), step.get('kind', ''))} "
                f"| {STATUS_FA.get(step.get('status'), step.get('status', ''))} "
                f"| {step.get('error') or ''} |"
            )
        lines.append("")

    lines += _sections_md(report)
    lines += [
        "---",
        "",
        "ساخته‌شده با ایجنت سئو. اعداد «فرصت» اولویت نسبی‌اند، نه پیش‌بینی ترافیک.",
    ]
    return "\n".join(lines)


def _headline_rows(report: dict[str, Any]) -> list[tuple[str, Any]]:
    headline = report.get("headline") or {}
    # A metric that was never measured is left out rather than shown as a
    # dash: an empty row invites the question "why is that blank", and the
    # answer is always "that step did not run", which the steps table says.
    return [(label, headline[key]) for key, label in HEADLINE_FA
            if headline.get(key) is not None]


def _sections_md(report: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    rankings = report.get("rankings") or {}
    if rankings.get("opportunities"):
        lines += ["## فرصت‌های رتبه", "",
                  "| کلمه کلیدی | جایگاه فعلی | فرصت |", "| --- | --- | --- |"]
        for row in rankings["opportunities"][:15]:
            position = row.get("position")
            lines.append(
                f"| {row.get('keyword', '')} "
                f"| {position if position is not None else 'پیدا نشد'} "
                f"| {row.get('opportunity', '')} |"
            )
        lines.append("")

    if rankings.get("top_competitors"):
        lines += ["## رقبا", "", "| دامنه | روی چند عبارت بالاتر است |", "| --- | --- |"]
        lines += [f"| {row.get('domain', '')} | {row.get('outranks_on', '')} |"
                  for row in rankings["top_competitors"]] + [""]

    content = report.get("content") or {}
    if content.get("top_gaps"):
        lines += ["## صفحه‌هایی که هنوز نوشته نشده‌اند", "",
                  "| کلمه کلیدی | تقاضا |", "| --- | --- |"]
        lines += [f"| {row.get('keyword', '')} | {row.get('demand', '')} |"
                  for row in content["top_gaps"]] + [""]

    trend = report.get("trend") or {}
    if trend.get("changes"):
        lines += ["## نسبت به اجرای قبلی", "",
                  "| سنجه | قبل | حالا | تغییر |", "| --- | --- | --- | --- |"]
        lines += [f"| {row.get('label_fa', row.get('metric', ''))} | {row.get('before', '')} "
                  f"| {row.get('after', '')} | {_arrow(row)} |"
                  for row in trend["changes"]] + [""]
        if trend.get("note"):
            lines += [trend["note"], ""]

    rivals = report.get("competitors") or {}
    if rivals.get("top_missing_themes"):
        lines += [f"## موضوع‌هایی که رقبا پوشش می‌دهند و شما نه "
                  f"({rivals.get('compared_against', 0)} رقیب مقایسه شد)", "",
                  "| موضوع |", "| --- |"]
        lines += [f"| {term} |" for term in rivals["top_missing_themes"]] + [""]

    links = report.get("links") or {}
    if links.get("top_opportunities"):
        lines += ["## صفحاتی که لینک داخلی کم دارند", "",
                  "| صفحه | لینک ورودی | اعتبار داخلی |", "| --- | --- | --- |"]
        lines += [f"| {row.get('url', '')} | {row.get('inlinks', '')} "
                  f"| {row.get('authority', '')} |"
                  for row in links["top_opportunities"]] + [""]

    plan = report.get("optimizer") or {}
    if plan.get("pages"):
        written = "متن آماده" if plan.get("written_by") == "ai" else "دستورالعمل"
        lines += [f"## پیشنهاد بازنویسی ({written})", "",
                  "| صفحه | تعداد اصلاح |", "| --- | --- |"]
        lines += [f"| {row.get('url', '')} | {row.get('fixes', '')} |"
                  for row in plan["pages"]] + [""]

    return lines


# ----------------------------------------------------------------------- html


def document(workflow: dict[str, Any]) -> str:
    """One file, no external requests, prints to A4.

    Built from the same data as the Markdown rather than by converting it:
    a Markdown-to-HTML step would put a parser between a report and its
    reader for no gain, and every escaping bug would live in that parser.
    """
    report = workflow.get("report") or {}
    summary = report.get("summary") or {}
    title = title_of(workflow)

    body: list[str] = [
        f"<h1>{_e(title)}</h1>",
        "<p class='meta'>"
        f"وضعیت: {_e(STATUS_FA.get(workflow.get('status'), workflow.get('status', '—')))}"
        f" · تاریخ: {_e(workflow.get('created_at') or '—')}"
        f" · شناسه: <code>{_e(workflow.get('workflow_id', '—'))}</code>"
        "</p>",
    ]
    if workflow.get("error"):
        body.append(f"<p class='error'>این تحلیل کامل نشد: {_e(workflow['error'])}</p>")

    if summary.get("text_fa"):
        source = "نوشته‌ی مدل" if summary.get("source") == "ai" else "قانون‌محور"
        body.append("<section><h2>خلاصه <span class='pill'>"
                    f"{_e(source)}</span></h2><p class='lede'>{_e(summary['text_fa'])}</p>")
        if summary.get("next_actions"):
            body.append("<h3>قدم‌های بعدی</h3><ol>")
            for action in summary["next_actions"]:
                body.append(
                    f"<li><strong>{_e(action.get('action', ''))}</strong><br>"
                    f"<span class='muted'>{_e(action.get('why', ''))} — "
                    f"تلاش: {_e(action.get('effort', '—'))}، "
                    f"اثر: {_e(action.get('impact', '—'))}</span></li>"
                )
            body.append("</ol>")
        if summary.get("watch_outs"):
            body.append("<h3>چیزهایی که این گزارش نمی‌داند</h3><ul class='muted'>")
            body += [f"<li>{_e(item)}</li>" for item in summary["watch_outs"]]
            body.append("</ul>")
        body.append("</section>")

    numbers = _headline_rows(report)
    if numbers:
        tiles = "".join(
            f"<div class='tile'><div class='value'>{_e(value)}</div>"
            f"<div class='name'>{_e(label)}</div></div>"
            for label, value in numbers
        )
        body.append(f"<section><h2>اعداد اصلی</h2><div class='tiles'>{tiles}</div></section>")

    steps = sorted(workflow.get("steps") or [], key=lambda s: s.get("position", 0))
    if steps:
        rows = "".join(
            f"<tr><td>{_e(s.get('position', ''))}</td>"
            f"<td>{_e(STEP_FA.get(s.get('kind'), s.get('kind', '')))}</td>"
            f"<td>{_e(STATUS_FA.get(s.get('status'), s.get('status', '')))}</td>"
            f"<td class='muted'>{_e(s.get('error') or '')}</td></tr>"
            for s in steps
        )
        body.append(f"<section><h2>مراحل</h2><table><tbody>{rows}</tbody></table></section>")

    body += _sections_html(report)
    body.append(
        "<footer>ساخته‌شده با ایجنت سئو. اعداد «فرصت» اولویت نسبی‌اند، "
        "نه پیش‌بینی ترافیک.</footer>"
    )

    return (
        "<!doctype html>\n"
        f"<html lang='fa' dir='rtl'><head><meta charset='utf-8'>"
        f"<title>{_e(title)}</title>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>{CSS}</style></head><body>{''.join(body)}</body></html>\n"
    )


def _sections_html(report: dict[str, Any]) -> list[str]:
    out: list[str] = []

    rankings = report.get("rankings") or {}
    if rankings.get("opportunities"):
        out.append(_table(
            "فرصت‌های رتبه",
            ["کلمه کلیدی", "جایگاه فعلی", "فرصت"],
            [[row.get("keyword", ""),
              row.get("position") if row.get("position") is not None else "پیدا نشد",
              row.get("opportunity", "")]
             for row in rankings["opportunities"][:15]],
            note="عدد فرصت اولویت نسبی است، نه پیش‌بینی ترافیک.",
        ))
    if rankings.get("top_competitors"):
        out.append(_table(
            "رقبا", ["دامنه", "روی چند عبارت بالاتر است"],
            [[row.get("domain", ""), row.get("outranks_on", "")]
             for row in rankings["top_competitors"]],
        ))

    content = report.get("content") or {}
    if content.get("top_gaps"):
        out.append(_table(
            "صفحه‌هایی که هنوز نوشته نشده‌اند", ["کلمه کلیدی", "تقاضا"],
            [[row.get("keyword", ""), row.get("demand", "")] for row in content["top_gaps"]],
        ))

    trend = report.get("trend") or {}
    if trend.get("changes"):
        out.append(_table(
            "نسبت به اجرای قبلی", ["سنجه", "قبل", "حالا", "تغییر"],
            [[row.get("label_fa", row.get("metric", "")), row.get("before", ""),
              row.get("after", ""), _arrow(row)]
             for row in trend["changes"]],
            note=trend.get("note") or "",
        ))

    rivals = report.get("competitors") or {}
    if rivals.get("top_missing_themes"):
        out.append(_table(
            "موضوع‌هایی که رقبا پوشش می‌دهند و شما نه", ["موضوع"],
            [[term] for term in rivals["top_missing_themes"]],
            note=f"{rivals.get('compared_against', 0)} رقیب مقایسه شد. عبارتی اینجا می‌آید "
                 "که دست‌کم دو رقیب رویش نوشته باشند و در سایت شما نباشد.",
        ))

    links = report.get("links") or {}
    if links.get("top_opportunities"):
        out.append(_table(
            "صفحاتی که لینک داخلی کم دارند", ["صفحه", "لینک ورودی", "اعتبار داخلی"],
            [[row.get("url", ""), row.get("inlinks", ""), row.get("authority", "")]
             for row in links["top_opportunities"]],
        ))

    plan = report.get("optimizer") or {}
    if plan.get("pages"):
        written = "متن آماده" if plan.get("written_by") == "ai" else "دستورالعمل"
        out.append(_table(
            f"پیشنهاد بازنویسی ({written})", ["صفحه", "تعداد اصلاح"],
            [[row.get("url", ""), row.get("fixes", "")] for row in plan["pages"]],
        ))
    return out


def _arrow(change: dict[str, Any]) -> str:
    """The change, with which way is *better* already decided.

    An arrow alone would leave the reader working out whether more issues is
    good news, and the whole point of the trend section is that they should
    not have to.
    """
    delta = change.get("change", 0)
    sign = f"+{delta}" if delta > 0 else str(delta)
    verdict = {"better": "بهتر", "worse": "بدتر"}.get(change.get("direction"), "بدون تغییر معنادار")
    return f"{sign} ({verdict})"


def _table(heading: str, columns: list[str], rows: list[list[Any]], note: str = "") -> str:
    head = "".join(f"<th>{_e(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    lede = f"<p class='lede'>{_e(note)}</p>" if note else ""
    return (f"<section><h2>{_e(heading)}</h2>{lede}"
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>")


def _e(value: Any) -> str:
    """Every value goes through here.

    A page title is attacker-controlled in the only sense that matters: it
    comes off someone else's website. A report that renders it raw is a report
    that can be made to run script in the reader's browser.
    """
    return html.escape("" if value is None else str(value), quote=True)


CSS = """
:root { --ink:#16191d; --muted:#5f6874; --line:#e3e6ea; --accent:#2f5fd0; --poor:#b02a37; }
* { box-sizing: border-box; }
body { margin:0 auto; padding:32px 24px 64px; max-width:900px; color:var(--ink);
  font-family: Vazirmatn, "Segoe UI", Tahoma, sans-serif; line-height:1.75; }
h1 { font-size:1.6rem; margin:0 0 4px; }
h2 { font-size:1.15rem; margin:28px 0 10px; }
h3 { font-size:1rem; margin:18px 0 8px; }
p.meta, .muted { color:var(--muted); }
p.meta { margin-top:0; font-size:.9rem; }
p.lede { color:var(--muted); margin-top:0; }
p.error { border:1px solid var(--poor); color:var(--poor); border-radius:8px; padding:10px 12px; }
code { direction:ltr; unicode-bidi:isolate; }
section { border-top:1px solid var(--line); padding-top:4px; }
table { width:100%; border-collapse:collapse; margin-top:6px; }
th, td { text-align:start; padding:7px 8px; border-bottom:1px solid var(--line);
  vertical-align:top; word-break:break-word; }
th { color:var(--muted); font-size:.85rem; font-weight:600; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }
.tile { border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
.tile .value { font-size:1.5rem; font-weight:700; }
.tile .name { color:var(--muted); font-size:.82rem; }
.pill { display:inline-block; padding:1px 10px; border-radius:999px; font-size:.78rem;
  border:1px solid var(--line); color:var(--muted); vertical-align:middle; }
ol li { margin-bottom:10px; }
footer { margin-top:36px; padding-top:12px; border-top:1px solid var(--line);
  color:var(--muted); font-size:.85rem; }
/* Printing is how this becomes a PDF, so it is a first-class layout, not an
   afterthought: no page breaks inside a table row, and sections stay whole. */
@page { size:A4; margin:16mm; }
@media print {
  body { padding:0; max-width:none; }
  section { break-inside:avoid; }
  tr, .tile { break-inside:avoid; }
}
"""
