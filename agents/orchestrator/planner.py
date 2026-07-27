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
    """Crawl the site, then research keywords for it.

    Sequential rather than parallel, and that is the point of having an
    orchestrator at all: the keyword step's seed can be filled in from what the
    crawl found, so it cannot start until the crawl has finished.
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

    return [
        Step(position=1, kind="crawl", params=crawl_params),
        Step(position=2, kind="keyword_research", params=research_params),
    ]


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


def seed_from_domain(url: str) -> str:
    host = urlparse(url if "//" in url else f"//{url}").hostname or url
    host = re.sub(r"^www\.", "", host)
    # Drop the public suffix: "example.com" and "example.co.uk" should both
    # research "example", not "example com".
    label = host.split(".")[0] if host else ""
    # Hyphens and underscores are word separators in domains.
    return re.sub(r"[-_]+", " ", label).strip()
