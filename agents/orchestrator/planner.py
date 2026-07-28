"""Turns a goal into an ordered list of steps.

Deliberately deterministic. The architecture calls this the "planner agent",
and it will eventually be able to ask a model what to do — but a plan is a
control-flow decision, and a control-flow decision that varies run to run is
not debuggable. So the rules decide the shape, and the model's job (when it is
added) is to fill in parameters, not to invent steps.

That also keeps the whole orchestrator runnable with no API key, which is the
same choice made for the engine's content suggestions.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


class UnknownGoal(ValueError):
    """No plan exists for this goal."""


@dataclass(frozen=True)
class Step:
    position: int
    kind: str                                   # crawl | keyword_research
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    params: dict[str, Any] = field(default_factory=dict)


def plan(goal: str, inputs: dict[str, Any]) -> list[Step]:
    if goal != "site_audit":
        raise UnknownGoal(f"no plan for goal {goal!r}")
    return _site_audit(inputs)


def _site_audit(inputs: dict[str, Any]) -> list[Step]:
    """Crawl the site, research keywords for it, then see where it ranks.

    Sequential rather than parallel, and that is the point of having an
    orchestrator at all. Each step needs the one before it: the keyword seed
    can be filled in from what the crawl found, and the rank check has nothing
    to check until research has produced keywords.

    The rewrite plan is last because it is the only step that proposes rather
    than reports: it is worth the most when everything before it has already
    said what is wrong.

    The link analysis comes fourth rather than second, even though it only
    needs the crawl. Ordering it late costs nothing — it is arithmetic over
    data already gathered — and keeps the expensive network steps starting as
    early as possible. The content analysis is last because it is the only
    step that needs two earlier ones at once: the crawl for what pages exist
    and the research for what people search.
    """
    start_url = (inputs.get("start_url") or "").strip()
    if not start_url:
        raise ValueError("site_audit needs a start_url")

    crawl_params: dict[str, Any] = {"start_url": start_url}
    for key in ("max_pages", "max_depth"):
        if inputs.get(key) is not None:
            crawl_params[key] = inputs[key]

    research_params: dict[str, Any] = {
        "lang": inputs.get("lang", "fa"),
        "country": inputs.get("country", "IR"),
    }
    # Left unset on purpose when the caller gave none: it is filled in after
    # the crawl, in resolve_seed below.
    if (inputs.get("seed") or "").strip():
        research_params["seed"] = inputs["seed"].strip()

    steps = [
        Step(position=1, kind="crawl", params=crawl_params),
        Step(position=2, kind="keyword_research", params=research_params),
        Step(position=3, kind="serp_check", params={
            "lang": research_params["lang"],
            "country": research_params["country"],
            "top_keywords": int(inputs.get("track_keywords") or DEFAULT_TRACKED),
        }),
        Step(position=4, kind="link_analysis", params={}),
        Step(position=5, kind="content_analysis", params={}),
        Step(position=6, kind="optimizer_plan", params={
            "pages": int(inputs.get("optimize_pages") or DEFAULT_OPTIMIZED),
        }),
    ]

    # Competitors are optional and, when asked for, expand into one crawl step
    # each followed by the comparison. One step per competitor rather than a
    # single step that fans out: the state machine already knows how to
    # dispatch a step and wait for its event, and a step that produces N jobs
    # would need a second kind of waiting — with its own locking, its own
    # partial-failure rules and its own bugs. Sequential is also kinder to the
    # sites being crawled, and the value here is that the comparison happens at
    # all, not that it happens in parallel.
    rivals = competitors(inputs)
    for url in rivals:
        steps.append(Step(
            position=len(steps) + 1,
            kind="competitor_crawl",
            params={"start_url": url, "max_pages": competitor_pages(inputs)},
        ))

    if rivals:
        steps.append(Step(position=len(steps) + 1, kind="competitor_check", params={}))

    return steps


def competitors(inputs: dict[str, Any]) -> list[str]:
    """The competitor urls to crawl, cleaned up and bounded.

    Deduplicated by origin: the same site given twice would be crawled twice
    and would then count twice towards "how many competitors write about this",
    which is the rule that stops one site's house style reading as a gap. Two
    urls on one host but different ports are two origins and stay two — the
    dedupe is about the same site listed twice, not about a shared machine.
    """
    urls: list[str] = []
    seen: set[str] = set()

    for raw in (inputs.get("competitors") or []):
        url = str(raw or "").strip()
        if not url:
            continue
        if "//" not in url:
            url = f"https://{url}"
        key = origin_of(url)
        if not key or key in seen:
            continue
        seen.add(key)
        urls.append(url)
        if len(urls) >= MAX_COMPETITORS:
            break

    return urls


def competitor_pages(inputs: dict[str, Any]) -> int:
    """How deep to crawl each competitor.

    Smaller than your own crawl by default, and deliberately so: the comparison
    reads rates and medians, both of which settle long before a whole site is
    fetched, and every page here is bandwidth taken from someone who did not
    ask to be studied.
    """
    asked = inputs.get("competitor_pages")
    if asked is None:
        return DEFAULT_COMPETITOR_PAGES
    return max(1, min(int(asked), MAX_COMPETITOR_PAGES))


# How many researched keywords to check rankings for. Every one is a live
# search request against a host that will start refusing if pushed, so this is
# a rate-limit decision as much as a useful-report decision.
DEFAULT_TRACKED = 10

# How many pages the rewrite plan covers. Bounded for the same reason: each one
# is model tokens, and more to the point it is work a person has to do.
DEFAULT_OPTIMIZED = 5
MAX_OPTIMIZED = 25
MAX_TRACKED = 50

# How many competitors one audit will crawl. Lower than the eight the
# competitor service accepts, because there the crawls already exist and here
# the workflow makes them: four competitors is four more sites fetched, one
# after another, every time the schedule fires.
MAX_COMPETITORS = 4
DEFAULT_COMPETITOR_PAGES = 20
MAX_COMPETITOR_PAGES = 200


def keywords_to_track(research_result: dict[str, Any] | None, limit: int) -> list[str]:
    """Pick what to rank-check from what research found.

    The keyword step reports its top terms already ordered by opportunity, so
    this takes the head of that list rather than re-deciding. Deduplicated
    case-insensitively because "کفش ورزشی" and "کفش ورزشی " are one query and
    two wasted requests.
    """
    keywords: list[str] = []
    seen: set[str] = set()

    for item in (research_result or {}).get("top_keywords", []):
        term = (item.get("keyword") if isinstance(item, dict) else str(item) or "").strip()
        if not term or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        keywords.append(term)
        if len(keywords) >= max(1, min(limit, MAX_TRACKED)):
            break

    return keywords


def resolve_seed(params: dict[str, Any], crawl_result: dict[str, Any] | None) -> str:
    """Work out what to research, now that the crawl has finished.

    An explicit seed always wins. Otherwise it comes from the crawled domain,
    which is a weak signal but a truthful one — better than refusing to run the
    step. A richer seed wants page titles, which live behind page.updated
    rather than in the crawl summary, so that is a later improvement and not
    something to fake here.
    """
    seed = (params.get("seed") or "").strip()
    if seed:
        return seed

    url = (crawl_result or {}).get("start_url") or params.get("start_url") or ""
    return seed_from_domain(url)


def domain_of(url: str) -> str:
    """The host a rank check should look for, without www."""
    host = urlparse(url if "//" in url else f"//{url}").hostname or ""
    return re.sub(r"^www\.", "", host).lower()


def origin_of(url: str) -> str:
    """Host and port, www dropped. What makes two urls the same competitor."""
    parsed = urlparse(url if "//" in url else f"//{url}")
    host = domain_of(url)
    if not host:
        return ""
    return f"{host}:{parsed.port}" if parsed.port else host


def seed_from_domain(url: str) -> str:
    host = domain_of(url) or url
    # Drop the public suffix: "example.com" and "example.co.uk" should both
    # research "example", not "example com".
    label = host.split(".")[0] if host else ""
    # Hyphens and underscores are word separators in domains.
    return re.sub(r"[-_]+", " ", label).strip()
