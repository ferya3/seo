"""SERP service: ranking analysis, job lifecycle, events, worker.

The provider is stubbed throughout. That is not laziness about coverage — it is
the honest boundary. Every search endpoint is blocked from the environment this
was written in, so the fetch cannot be exercised; what can be, and is, is every
decision made about the results once they arrive.

The one parser test below runs against a fixture written by hand from the
documented markup, so it proves the extraction logic handles that shape — not
that the shape matches what DuckDuckGo serves today. providers.py says the same
thing where someone changing it will read it.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "engine"))

from services.serp import analyze, providers  # noqa: E402
from services.serp.providers import Result  # noqa: E402
from shared.contracts import validate_event  # noqa: E402
from shared.events import Envelope  # noqa: E402

MINE = "example.com"


def results(*domains: str) -> list[Result]:
    return [
        Result(position=i + 1, url=f"https://{d}/page", domain=d, title=f"{d} title")
        for i, d in enumerate(domains)
    ]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SERP_DATA_DIR", str(tmp_path / "serp"))
    monkeypatch.setenv("EVENTS_ENABLED", "0")

    from services.serp import api
    from services.serp.store import CheckStore

    api.store = CheckStore(tmp_path / "serp")
    api._publisher = None
    # No sleeping between lookups in tests.
    monkeypatch.setattr(api, "DELAY_SECONDS", 0.0)
    yield api


@pytest.fixture
def client(isolated):
    return TestClient(isolated.app)


@pytest.fixture
def fake_provider(isolated, monkeypatch):
    """A provider that ranks example.com third for everything."""
    def fetch(query, lang="fa", country="IR", limit=10):
        return results("rival.com", "other.ir", MINE, "shop.net")

    monkeypatch.setitem(providers.PROVIDERS, "duckduckgo", fetch)
    return fetch


# ------------------------------------------------------------------ analysis


def test_a_domain_is_found_at_its_position():
    ranking = analyze.rank("کفش", results("a.com", MINE, "b.com"), MINE)
    assert ranking.position == 2
    assert ranking.url == f"https://{MINE}/page"


def test_not_ranking_is_none_not_zero():
    """None and 0 must not be confused: one means absent, the other would sort
    as better than first place."""
    assert analyze.rank("کفش", results("a.com", "b.com"), MINE).position is None


def test_a_subdomain_counts_as_the_same_site():
    ranking = analyze.rank("کفش", results("a.com", f"blog.{MINE}"), MINE)
    assert ranking.position == 2


def test_a_lookalike_domain_does_not_count():
    """Suffix matching alone would make notexample.com match example.com, and
    report a competitor's ranking as your own."""
    assert analyze.rank("کفش", results("a.com", "notexample.com"), MINE).position is None


def test_www_is_ignored_on_both_sides():
    ranking = analyze.rank("کفش", results("www.example.com"), "www.example.com")
    assert ranking.position == 1


def test_competitors_above_are_listed_in_order_without_repeats():
    ranking = analyze.rank(
        "کفش", results("rival.com", "rival.com", "other.ir", MINE), MINE
    )
    # A competitor holding two of the top three is one competitor to beat.
    assert ranking.competitors_above == ["rival.com", "other.ir"]


def test_nothing_is_above_you_when_you_are_first():
    assert analyze.rank("کفش", results(MINE, "a.com"), MINE).competitors_above == []


def test_no_target_domain_means_no_ranking():
    ranking = analyze.rank("کفش", results("a.com"), None)
    assert ranking.position is None
    assert len(ranking.results) == 1


@pytest.mark.parametrize(
    "position,expected",
    [(None, 100), (1, 0)],
)
def test_opportunity_is_highest_when_unranked_and_zero_at_the_top(position, expected):
    assert analyze.opportunity(position) == expected


def test_opportunity_falls_as_position_improves():
    """The number exists to order work: a page at 8 is worth more effort than
    one at 2, because there is more to gain."""
    scores = [analyze.opportunity(p) for p in (2, 3, 5, 8)]
    assert scores == sorted(scores)


# ------------------------------------------------------------------- summary


def test_the_summary_counts_ranked_and_missing():
    rankings = [
        analyze.rank("a", results("rival.com", MINE), MINE),
        analyze.rank("b", results("rival.com"), MINE),
        analyze.Ranking(keyword="c", position=None, error="ProviderError: blocked"),
    ]
    summary = analyze.summarise(rankings, MINE)

    assert summary["keywords_checked"] == 3
    assert summary["keywords_ranked"] == 1
    assert summary["keywords_missing"] == 1
    assert summary["failures"] == [{"keyword": "c", "error": "ProviderError: blocked"}]


def test_the_average_position_ignores_keywords_that_do_not_rank():
    """Counting "not ranking" as some large number would invent data; leaving
    it out makes the average mean "where we rank, when we rank"."""
    rankings = [
        analyze.rank("a", results(MINE), MINE),                    # 1
        analyze.rank("b", results("x.com", "y.com", MINE), MINE),  # 3
        analyze.rank("c", results("x.com"), MINE),                 # absent
    ]
    assert analyze.summarise(rankings, MINE)["average_position"] == 2.0


def test_the_average_is_none_when_nothing_ranks():
    assert analyze.summarise([analyze.rank("a", results("x.com"), MINE)], MINE)[
        "average_position"
    ] is None


def test_competitors_are_ranked_by_how_often_they_beat_you():
    rankings = [
        analyze.rank("a", results("rival.com", "other.ir", MINE), MINE),
        analyze.rank("b", results("rival.com", MINE), MINE),
        analyze.rank("c", results("rival.com", MINE), MINE),
    ]
    top = analyze.summarise(rankings, MINE)["top_competitors"]
    assert top[0] == {"domain": "rival.com", "outranks_on": 3}
    assert top[1] == {"domain": "other.ir", "outranks_on": 1}


def test_your_own_subdomains_are_not_your_competitors():
    rankings = [analyze.rank("a", results(f"blog.{MINE}", "rival.com", MINE), MINE)]
    domains = [c["domain"] for c in analyze.summarise(rankings, MINE)["top_competitors"]]
    assert f"blog.{MINE}" not in domains


def test_opportunities_lead_with_the_biggest_win():
    rankings = [
        analyze.rank("ranked-first", results(MINE), MINE),
        analyze.rank("not-ranked", results("x.com"), MINE),
        analyze.rank("ranked-third", results("x.com", "y.com", MINE), MINE),
    ]
    order = [o["keyword"] for o in analyze.summarise(rankings, MINE)["opportunities"]]
    assert order[0] == "not-ranked"
    assert order[-1] == "ranked-first"


def test_the_best_position_is_reported():
    rankings = [
        analyze.rank("a", results("x.com", MINE), MINE),
        analyze.rank("b", results(MINE), MINE),
    ]
    assert analyze.summarise(rankings, MINE)["best"] == {
        "keyword": "b", "position": 1, "url": f"https://{MINE}/page"
    }


# ------------------------------------------------------------------ provider


def test_domain_extraction_drops_www_and_the_scheme():
    assert providers.domain_of("https://www.Example.com/a/b?c=1") == "example.com"


def test_a_redirector_link_is_unwrapped():
    """The results page wraps outbound links; reporting the wrapper would make
    every result look like it belonged to the search engine."""
    wrapped = "/l/?uddg=https%3A%2F%2Fexample.com%2Fshoes&rut=abc"
    assert providers._unwrap(wrapped) == "https://example.com/shoes"


def test_the_parser_reads_a_result_block():
    """Fixture written by hand from the documented markup — this proves the
    extraction handles that shape, not that the shape is current."""
    html = """
    <div class="result results_links web-result">
      <h2 class="result__title"><a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2F">
        فروشگاه کفش</a></h2>
      <a class="result__snippet">بهترین قیمت کفش ورزشی</a>
    </div>
    <div class="result result--ad">
      <a class="result__a" href="https://ad.example/">تبلیغ</a>
    </div>
    <div class="result web-result">
      <a class="result__a" href="https://rival.com/x">رقیب</a>
    </div>
    """
    parsed = providers.parse_duckduckgo(html)

    assert [r.domain for r in parsed] == ["example.com", "rival.com"]
    assert [r.position for r in parsed] == [1, 2]
    assert parsed[0].title == "فروشگاه کفش"
    assert parsed[0].snippet == "بهترین قیمت کفش ورزشی"


def test_an_unknown_provider_is_refused():
    with pytest.raises(providers.ProviderError):
        providers.get("mystery-engine")


# ----------------------------------------------------------------- the check


def test_one_bad_keyword_does_not_lose_the_others(isolated):
    def flaky(query, lang="fa", country="IR", limit=10):
        if query == "bad":
            raise providers.ProviderError("blocked")
        return results(MINE)

    rankings = isolated.check_keywords(flaky, ["good", "bad", "also-good"], MINE, delay=0)

    assert [r.keyword for r in rankings] == ["good", "bad", "also-good"]
    assert rankings[1].error is not None
    assert rankings[0].position == 1 and rankings[2].position == 1


def test_lookups_are_paced(isolated, monkeypatch):
    """Dozens of requests to one host back to back is what gets a client
    blocked, which breaks the service far worse than being slow."""
    slept: list[float] = []
    monkeypatch.setattr("services.serp.api.time.sleep", lambda s: slept.append(s))

    isolated.check_keywords(lambda q, **k: results(MINE), ["a", "b", "c"], MINE, delay=2.0)

    # Between queries, not before the first.
    assert slept == [2.0, 2.0]


# --------------------------------------------------------------------- api


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["service"] == "serp-service"


def test_a_check_runs_end_to_end_and_is_readable(client, fake_provider):
    response = client.post("/v1/checks", json={
        "target_domain": "example.com", "keywords": ["کفش", "کفش ورزشی"],
    })
    assert response.status_code == 202
    check_id = response.json()["check_id"]

    result = client.get(f"/v1/checks/{check_id}").json()
    assert result["status"] == "completed"
    assert result["report"]["keywords_ranked"] == 2
    assert result["report"]["average_position"] == 3.0

    assert any(c["check_id"] == check_id for c in client.get("/v1/checks").json())


def test_a_url_is_accepted_where_a_domain_is_expected(client, fake_provider):
    """People paste the address bar. Storing "https://www.example.com/" as the
    target would then never match any result."""
    check_id = client.post("/v1/checks", json={
        "target_domain": "https://www.example.com/shop", "keywords": ["کفش"],
    }).json()["check_id"]

    assert client.get(f"/v1/checks/{check_id}").json()["target_domain"] == "example.com"


def test_unknown_check_is_404(client):
    assert client.get(f"/v1/checks/{uuid.uuid4()}").status_code == 404


@pytest.mark.parametrize("body", [
    {},                                                        # nothing
    {"target_domain": "example.com"},                          # no keywords
    {"target_domain": "example.com", "keywords": []},          # empty list
    {"keywords": ["کفش"]},                                     # no domain
    {"target_domain": "example.com", "keywords": ["x"] * 51},  # over the cap
])
def test_bad_input_is_rejected(client, body):
    assert client.post("/v1/checks", json=body).status_code == 422


def test_blank_keywords_are_rejected(client):
    assert client.post(
        "/v1/checks", json={"target_domain": "example.com", "keywords": ["  ", ""]}
    ).status_code == 422


# ------------------------------------------------------------------- events


def test_completion_emits_a_contract_valid_event(isolated, fake_provider, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))

    check_id = str(uuid.uuid4())
    isolated.store.create(check_id, MINE)
    isolated.run_check(check_id, {"target_domain": MINE, "keywords": ["کفش"]})

    event_type, payload = events[0]
    assert event_type == "serp.checked"
    validate_event("serp.checked", payload)
    assert payload["keywords_ranked"] == 1
    assert payload["result_url"] == f"/v1/checks/{check_id}"
    # Carried on the event so a summary can say where the site is winning
    # without reopening the full check.
    assert payload["best"]["keyword"] == "کفش"


def test_a_failed_run_still_reports(isolated, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(isolated, "_emit", lambda t, p, c, f: events.append((t, p)))
    monkeypatch.setattr(isolated.providers, "get", lambda n: (_ for _ in ()).throw(
        providers.ProviderError("no provider")
    ))

    check_id = str(uuid.uuid4())
    isolated.store.create(check_id, MINE)
    record = isolated.run_check(check_id, {"target_domain": MINE, "keywords": ["کفش"]})

    assert record.status == "failed"
    validate_event("serp.checked", events[0][1])
    assert events[0][1]["status"] == "failed"


def test_an_invalid_payload_is_never_published(isolated, monkeypatch):
    published: list[str] = []

    class Recorder:
        def emit(self, event_type, payload, **kwargs):
            published.append(event_type)

        def publish(self, envelope):
            published.append(envelope.type)

    monkeypatch.setattr(isolated, "publisher", lambda: Recorder())
    isolated._emit("serp.checked", {"check_id": "incomplete"}, None, {})
    assert published == []


# ------------------------------------------------------------------- worker


def test_the_worker_runs_a_requested_check(isolated, monkeypatch):
    from services.serp import worker

    calls: list[str] = []
    monkeypatch.setattr(worker.api, "run_check", lambda cid, payload, **kw: calls.append(cid))
    worker._seen.clear()

    check_id = str(uuid.uuid4())
    worker.handle(Envelope(
        type="serp.check_requested",
        payload={"check_id": check_id, "target_domain": MINE, "keywords": ["کفش"]},
        producer="orchestrator",
    ))
    assert calls == [check_id]


def test_the_worker_ignores_a_redelivered_event(isolated, monkeypatch):
    from services.serp import worker

    calls: list[str] = []
    monkeypatch.setattr(worker.api, "run_check", lambda cid, payload, **kw: calls.append(cid))
    worker._seen.clear()

    envelope = Envelope(
        type="serp.check_requested",
        payload={"check_id": str(uuid.uuid4()), "target_domain": MINE, "keywords": ["کفش"]},
        producer="orchestrator",
    )
    worker.handle(envelope)
    worker.handle(envelope)
    assert len(calls) == 1


def test_the_worker_carries_tenancy_from_the_envelope(isolated, monkeypatch):
    from services.serp import worker

    monkeypatch.setattr(worker.api, "run_check", lambda cid, payload, **kw: None)
    worker._seen.clear()

    check_id, tenant, project = (str(uuid.uuid4()) for _ in range(3))
    worker.handle(Envelope(
        type="serp.check_requested",
        payload={"check_id": check_id, "target_domain": MINE, "keywords": ["کفش"]},
        producer="orchestrator",
        tenant_id=tenant,
        project_id=project,
    ))

    record = isolated.store.get(check_id)
    assert (record.tenant_id, record.project_id) == (tenant, project)


def test_the_worker_dead_letters_a_malformed_request(isolated):
    from services.serp import worker

    worker._seen.clear()
    with pytest.raises(ValueError):
        worker.handle(Envelope(
            type="serp.check_requested", payload={"keywords": ["کفش"]}, producer="orchestrator"
        ))
