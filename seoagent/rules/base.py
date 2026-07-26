"""Rule registry.

A rule is a plain function that receives the whole :class:`SiteContext` and
yields :class:`Issue` objects. Registering is a one-line decorator, which keeps
adding new checks cheap as Google's guidance moves.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from ..models import Category, Issue, Severity, SiteContext

RuleFn = Callable[[SiteContext], Iterable[Issue]]

_REGISTRY: list[tuple[str, RuleFn]] = []


def rule(name: str) -> Callable[[RuleFn], RuleFn]:
    def decorator(fn: RuleFn) -> RuleFn:
        _REGISTRY.append((name, fn))
        return fn

    return decorator


def all_rules() -> list[tuple[str, RuleFn]]:
    return list(_REGISTRY)


def run_all(ctx: SiteContext) -> list[Issue]:
    issues: list[Issue] = []
    for name, fn in _REGISTRY:
        try:
            issues.extend(fn(ctx) or [])
        except Exception as exc:  # one bad rule must not sink the whole audit
            ctx.notes.append(f"قانون «{name}» با خطا متوقف شد: {type(exc).__name__}: {exc}")
    issues.sort(key=lambda i: (i.severity, -i.affected_count, i.id))
    return issues


def make_issue(
    issue_id: str,
    title_en: str,
    title_fa: str,
    detail_fa: str,
    fix_fa: str,
    severity: Severity,
    category: Category,
    urls: Iterable[str] = (),
    evidence: str = "",
    docs: str = "",
) -> Issue:
    return Issue(
        id=issue_id,
        title_en=title_en,
        title_fa=title_fa,
        detail_fa=detail_fa,
        fix_fa=fix_fa,
        severity=severity,
        category=category,
        urls=list(urls),
        evidence=evidence,
        docs=docs,
    )


def sample(urls: Iterable[str], limit: int = 5) -> str:
    """Render a handful of URLs for the evidence line."""
    items = list(urls)[:limit]
    return "، ".join(items)


def iter_html_pages(ctx: SiteContext) -> Iterator:
    return iter(ctx.html_pages)
