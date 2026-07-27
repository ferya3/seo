"""The state machine: dispatch a step, wait for its event, advance.

Everything here is driven by events already on the bus. The orchestrator never
calls a service over HTTP — it publishes `crawl.requested` and
`keyword.research_requested`, which the existing workers already consume, and
it listens for `crawl.completed` and `keyword.researched`. That is why those
contracts were written before anything consumed them.

Three properties this has to hold, because delivery is at-least-once and there
may be more than one orchestrator:

  * A completion event that arrives twice advances the workflow once. The step
    transition is `dispatched -> completed`, done as a conditional UPDATE, so
    the second arrival matches no row and stops there.
  * Two events landing at once cannot both dispatch the next step: advancing
    happens under a row lock on the workflow.
  * A completion event for a job that belongs to no workflow is ignored, not an
    error — crawls started directly through the API emit the same event.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.contracts import ContractError, validate_event
from shared.store import PendingEvent

from . import planner, summary
from .store import Workflow, WorkflowStore

log = logging.getLogger(__name__)
SERVICE = "orchestrator"

# Which completion event finishes which kind of step, and the id field that
# ties it back. Adding a step kind means adding a row here and nothing else.
COMPLETIONS = {
    "crawl.completed": ("crawl", "crawl_id"),
    "keyword.researched": ("keyword_research", "research_id"),
    "serp.checked": ("serp_check", "check_id"),
    "links.analyzed": ("link_analysis", "analysis_id"),
    "content.analyzed": ("content_analysis", "analysis_id"),
}


def start(store: WorkflowStore, workflow_id: str, goal: str, inputs: dict[str, Any],
          tenant_id: str | None = None, project_id: str | None = None) -> Workflow:
    """Plan the work and dispatch the first step."""
    steps = planner.plan(goal, inputs)
    store.create(
        workflow_id, goal, inputs,
        [(s.position, s.kind, s.job_id) for s in steps],
        tenant_id=tenant_id, project_id=project_id,
    )
    store.set_status(workflow_id, "running")
    advance(store, workflow_id)
    return store.get(workflow_id)


def on_completion(store: WorkflowStore, event_type: str, payload: dict[str, Any]) -> str | None:
    """Handle a completion event. Returns the workflow it advanced, if any."""
    kind, id_field = COMPLETIONS[event_type]
    job_id = payload.get(id_field)
    if not job_id:
        raise ValueError(f"{event_type} carries no {id_field}")

    failed = payload.get("status") == "failed"
    workflow_id = store.mark_step(
        job_id,
        "failed" if failed else "completed",
        result=_step_result(kind, payload),
        error=payload.get("error"),
    )

    if workflow_id is None:
        # Either this job was never part of a workflow, or the event is a
        # redelivery of one already handled. Both are ordinary.
        log.debug("%s for %s matched no dispatched step", event_type, job_id)
        return None

    advance(store, workflow_id)
    return workflow_id


def advance(store: WorkflowStore, workflow_id: str) -> None:
    """Dispatch the next step, or finish the workflow.

    Held under a row lock the whole way: read the state, decide, write. Doing
    the read outside the lock is what would let two instances both see the same
    pending step and dispatch it twice.
    """
    with store.claim(workflow_id) as conn:
        workflow = store.load(conn, workflow_id)
        if workflow is None or workflow.status in ("completed", "failed"):
            return

        if any(s.status == "failed" for s in workflow.steps):
            _finish(store, conn, workflow, "failed", _first_error(workflow))
            return

        if any(s.status == "dispatched" for s in workflow.steps):
            return                                    # still waiting on one

        step = workflow.next_pending()
        if step is None:
            _finish(store, conn, workflow, "completed", None)
            return

        try:
            event = _dispatch_event(workflow, step)
        except NoWorkToDo as reason:
            # Nothing to do is an answer, not a fault. The step is marked
            # skipped and the workflow carries on to whatever comes next.
            log.info("workflow %s skipping step %s: %s", workflow_id, step.position, reason)
            store.skip_step(step.job_id, str(reason), conn=conn)
            workflow = store.load(conn, workflow_id)
            step = workflow.next_pending()
            if step is None:
                _finish(store, conn, workflow, "completed", None)
                return
            try:
                event = _dispatch_event(workflow, step)
            except Exception as exc:
                _finish(store, conn, workflow, "failed", f"{type(exc).__name__}: {exc}")
                return
        except Exception as exc:
            log.exception("workflow %s could not dispatch step %s", workflow_id, step.position)
            _finish(store, conn, workflow, "failed", f"{type(exc).__name__}: {exc}")
            return

        # Staged, not published: the orchestrator writes to the outbox for the
        # same reason the services do, so a crash here cannot leave a step
        # marked dispatched with nothing ever sent. Same connection as the
        # lock, so the whole decision commits or none of it does.
        store.set_status(workflow_id, "running", events=[event], conn=conn)
        store.mark_dispatched(step.job_id, conn=conn)


def _dispatch_event(workflow: Workflow, step) -> PendingEvent:
    if step.kind == "crawl":
        event_type = "crawl.requested"
        payload = {"crawl_id": step.job_id, **_crawl_params(workflow)}
    elif step.kind == "keyword_research":
        event_type = "keyword.research_requested"
        payload = {"research_id": step.job_id, **_research_params(workflow)}
    elif step.kind == "serp_check":
        event_type = "serp.check_requested"
        payload = {"check_id": step.job_id, **_serp_params(workflow)}
    elif step.kind == "link_analysis":
        event_type = "links.analysis_requested"
        payload = {"analysis_id": step.job_id, **_links_params(workflow)}
    elif step.kind == "content_analysis":
        event_type = "content.analysis_requested"
        payload = {"analysis_id": step.job_id, **_content_params(workflow)}
    else:                                             # pragma: no cover - guarded by the schema
        raise ValueError(f"unknown step kind {step.kind!r}")

    # Validated before staging, not after: an invalid payload in the outbox
    # would be published later by the relay with no idea it was wrong.
    validate_event(event_type, payload)
    return PendingEvent(
        type=event_type,
        payload=payload,
        producer=SERVICE,
        correlation_id=workflow.workflow_id,
    )


def _crawl_params(workflow: Workflow) -> dict[str, Any]:
    inputs = workflow.inputs
    params: dict[str, Any] = {"start_url": inputs["start_url"]}
    for key in ("max_pages", "max_depth"):
        if inputs.get(key) is not None:
            params[key] = inputs[key]
    return params


def _research_params(workflow: Workflow) -> dict[str, Any]:
    """The step that makes this an orchestrator rather than a fan-out: the seed
    can come from what the crawl found, so it is only knowable now."""
    crawl_step = next((s for s in workflow.steps if s.kind == "crawl"), None)
    seed = planner.resolve_seed(
        {"seed": workflow.inputs.get("seed"), "start_url": workflow.inputs.get("start_url")},
        crawl_step.result if crawl_step else None,
    )
    if not seed:
        raise ValueError("could not work out what to research")

    return {
        "seed": seed,
        "lang": workflow.inputs.get("lang", "fa"),
        "country": workflow.inputs.get("country", "IR"),
    }


def _serp_params(workflow: Workflow) -> dict[str, Any]:
    """The step with a real data dependency: what to rank-check is whatever
    research turned up, so this cannot be planned in advance."""
    research = next(
        (s.result for s in workflow.steps if s.kind == "keyword_research" and s.result), None
    )
    limit = int(workflow.inputs.get("track_keywords") or planner.DEFAULT_TRACKED)
    keywords = planner.keywords_to_track(research, limit)
    if not keywords:
        # Research finding nothing is not a workflow failure — it is a real
        # answer about a site. Raising here would turn "no keywords" into
        # "everything broke".
        raise NoWorkToDo("research produced no keywords to rank-check")

    domain = planner.domain_of(workflow.inputs.get("start_url", ""))
    if not domain:
        raise ValueError("could not work out which domain to rank-check")

    return {
        "target_domain": domain,
        "keywords": keywords,
        "lang": workflow.inputs.get("lang", "fa"),
        "country": workflow.inputs.get("country", "IR"),
    }


def _links_params(workflow: Workflow) -> dict[str, Any]:
    """Which crawl to analyse — the one this workflow just ran.

    The service fetches the report itself; all that travels is the id.
    """
    crawl = next((s.result for s in workflow.steps if s.kind == "crawl" and s.result), None)
    crawl_id = (crawl or {}).get("crawl_id")
    if not crawl_id:
        # The crawl failed or was skipped, so there is no graph to analyse.
        # Nothing to do is not a failure — see NoWorkToDo.
        raise NoWorkToDo("no crawl to analyse")
    return {"crawl_id": crawl_id}


def _content_params(workflow: Workflow) -> dict[str, Any]:
    """The only step that needs two earlier ones at once.

    Coverage is a question about a crawl *and* a keyword study; with either
    one missing there is no question to ask, so the step is skipped rather
    than failed.
    """
    crawl = next((s.result for s in workflow.steps if s.kind == "crawl" and s.result), None)
    research = next(
        (s.result for s in workflow.steps if s.kind == "keyword_research" and s.result), None
    )
    crawl_id = (crawl or {}).get("crawl_id")
    research_id = (research or {}).get("research_id")

    if not crawl_id or not research_id:
        raise NoWorkToDo("content coverage needs both a crawl and a keyword study")
    return {"crawl_id": crawl_id, "research_id": research_id}


class NoWorkToDo(Exception):
    """A step has nothing to act on. The workflow finishes, it does not fail."""


def _finish(
    store: WorkflowStore, conn, workflow: Workflow, status: str, error: str | None
) -> None:
    steps = [
        {
            "position": s.position,
            "kind": s.kind,
            "job_id": s.job_id,
            "status": s.status,
            "error": s.error,
            "result_url": _result_url(s),
        }
        for s in sorted(workflow.steps, key=lambda s: s.position)
    ]
    # One list, used by both the report and the event. Built twice, they drift.
    report = _report(workflow, status, error, steps)
    payload = {
        "workflow_id": workflow.workflow_id,
        "goal": workflow.goal,
        "status": status,
        "error": error,
        "steps": steps,
        "headline": report["headline"],
        "result_url": f"/v1/workflows/{workflow.workflow_id}",
    }

    events: list[PendingEvent] = []
    try:
        validate_event("workflow.completed", payload)
        events.append(PendingEvent(
            type="workflow.completed", payload=payload, producer=SERVICE,
            correlation_id=workflow.workflow_id,
        ))
    except ContractError:
        log.exception("refusing to publish invalid workflow.completed")

    store.set_status(
        workflow.workflow_id, status, report=report, error=error, events=events, conn=conn
    )


def _report(
    workflow: Workflow, status: str, error: str | None, steps: list[dict[str, Any]]
) -> dict[str, Any]:
    """What the workflow produced, gathered in one place.

    Summaries only, and a url per step. The crawl report alone is megabytes;
    copying it in here would duplicate megabytes into a second table that has
    no way to stay in step with the first.
    """
    def result_of(kind: str) -> dict[str, Any]:
        return next((s.result for s in workflow.steps if s.kind == kind and s.result), None) or {}

    crawl = result_of("crawl")
    research = result_of("keyword_research")
    serp = result_of("serp_check")
    links = result_of("link_analysis")
    content = result_of("content_analysis")

    report = {
        "goal": workflow.goal,
        "status": status,
        "error": error,
        "steps": steps,
        "headline": {
            "overall_score": crawl.get("overall_score"),
            "total_issues": crawl.get("total_issues"),
            "keywords_found": research.get("total"),
            "keywords_ranked": serp.get("keywords_ranked"),
            "average_position": serp.get("average_position"),
            "orphan_pages": links.get("orphan_count"),
            "keyword_coverage": content.get("coverage"),
        },
        "crawl": crawl,
        "keywords": research,
        "rankings": serp,
        "links": links,
        "content": content,
    }
    # The deterministic summary only — this runs while the workflow row is
    # locked, so it may not touch the network. The model-written one replaces
    # it afterwards, off the lock, from the worker. See summary.py.
    report["summary"] = summary.deterministic(report)
    return report


def _step_result(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the few fields worth reporting, not the whole event."""
    if kind == "crawl":
        stats = payload.get("stats", {})
        return {
            "crawl_id": payload.get("crawl_id"),
            "start_url": payload.get("start_url"),
            "overall_score": payload.get("overall_score"),
            "grade": payload.get("grade"),
            "total_issues": stats.get("total_issues"),
            "pages_crawled": stats.get("pages_crawled"),
            "result_url": payload.get("result_url"),
        }
    if kind == "keyword_research":
        return {
            "research_id": payload.get("research_id"),
            "seed": payload.get("seed"),
            "total": payload.get("total"),
            "cluster_count": payload.get("cluster_count"),
            # Kept rather than trimmed to a headline: the next step reads this
            # to decide what to rank-check.
            "top_keywords": payload.get("top_keywords", [])[:25],
            "result_url": payload.get("result_url"),
        }
    if kind == "content_analysis":
        return {
            "analysis_id": payload.get("analysis_id"),
            "crawl_id": payload.get("crawl_id"),
            "research_id": payload.get("research_id"),
            "keywords": payload.get("keywords"),
            "covered": payload.get("covered"),
            "coverage": payload.get("coverage"),
            "gap_count": payload.get("gap_count"),
            "cannibalisation_count": payload.get("cannibalisation_count"),
            "top_gaps": payload.get("top_gaps", [])[:10],
            "result_url": payload.get("result_url"),
        }
    if kind == "link_analysis":
        return {
            "analysis_id": payload.get("analysis_id"),
            "crawl_id": payload.get("crawl_id"),
            "pages": payload.get("pages"),
            "internal_links": payload.get("internal_links"),
            "orphan_count": payload.get("orphan_count"),
            "dead_end_count": payload.get("dead_end_count"),
            "broken_target_count": payload.get("broken_target_count"),
            "max_depth": payload.get("max_depth"),
            "average_inlinks": payload.get("average_inlinks"),
            "top_opportunities": payload.get("top_opportunities", [])[:10],
            "result_url": payload.get("result_url"),
        }
    return {
        "check_id": payload.get("check_id"),
        "target_domain": payload.get("target_domain"),
        "keywords_checked": payload.get("keywords_checked"),
        "keywords_ranked": payload.get("keywords_ranked"),
        "average_position": payload.get("average_position"),
        "best": payload.get("best"),
        "top_competitors": payload.get("top_competitors", [])[:5],
        "opportunities": payload.get("opportunities", [])[:10],
        "result_url": payload.get("result_url"),
    }


def _result_url(step) -> str | None:
    return (step.result or {}).get("result_url")


def _first_error(workflow: Workflow) -> str:
    failed = [s for s in workflow.steps if s.status == "failed"]
    step = min(failed, key=lambda s: s.position)
    return f"step {step.position} ({step.kind}) failed: {step.error or 'no reason given'}"
