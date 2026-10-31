"""Unit tests for the pure pieces: gateway retries, plan validation, verdict rule, origins, quotes, fetch guard."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from app.llm.gateway import CallContext, ModelCallFailed, ModelGateway, cost_of
from app.llm.prompts import Prompt
from app.llm.providers import ProviderError, ProviderResponse, ScriptedProvider, classify_status
from app.llm.schemas import Plan, VerifierOutput, WriterOutput
from app.research.planning import PlanError, plan_to_tasks, validate_plan
from app.research.verifier import EvidenceView, best_sentence, decide_verdict, origin_map
from app.research.web import (ToolError, check_url_allowed, extract_page, quote_in_text, registrable_domain,
                              shingle_fingerprint)
from app.research.writer import citation_check

PROMPT = Prompt("verify", "verify-test", "system", "user")
OK = ProviderResponse(text=json.dumps({"judgements": []}), input_tokens=100, output_tokens=20)


async def _no_sleep(_):
    return None


def _gateway(*providers, records=None):
    records = records if records is not None else []
    return ModelGateway(list(providers), records.append, max_attempts=3, sleep=_no_sleep), records


def _queue(*items, model="m1"):
    items = list(items)
    return ScriptedProvider(lambda _req: items.pop(0), model=model)


def test_gateway_retries_transient_errors_and_logs_every_attempt():
    gateway, records = _gateway(_queue(ProviderError("server_error", "503", retryable=True), OK))
    result = asyncio.run(gateway.generate(PROMPT, VerifierOutput, CallContext(run_id=None)))
    assert result.attempts == 2
    assert [r.status for r in records] == ["error", "success"]
    assert records[1].prompt_version == "verify-test" and records[1].input_tokens == 100


def test_gateway_does_not_retry_non_retryable():
    gateway, records = _gateway(_queue(ProviderError("auth_error", "bad key", retryable=False), OK))
    with pytest.raises(ModelCallFailed) as exc:
        asyncio.run(gateway.generate(PROMPT, VerifierOutput))
    assert exc.value.category == "auth_error" and len(records) == 1


def test_gateway_switches_model_when_daily_quota_is_exhausted():
    first = _queue(ProviderError("quota_exhausted", "per day", retryable=False), model="m1")
    second = _queue(OK, model="m2")
    gateway, records = _gateway(first, second)
    result = asyncio.run(gateway.generate(PROMPT, VerifierOutput))
    assert result.model == "m2"
    assert [(r.model, r.status) for r in records] == [("m1", "error"), ("m2", "success")]


def test_gateway_retries_invalid_json_once_only():
    bad = ProviderResponse(text="not json")
    gateway, records = _gateway(_queue(bad, bad, OK))
    with pytest.raises(ModelCallFailed) as exc:
        asyncio.run(gateway.generate(PROMPT, VerifierOutput))
    assert exc.value.category == "invalid_json" and len(records) == 2


def test_gateway_respects_daily_limit():
    gateway, records = _gateway(_queue(OK))
    gateway.allow_call = lambda: False
    with pytest.raises(ModelCallFailed) as exc:
        asyncio.run(gateway.generate(PROMPT, VerifierOutput))
    assert exc.value.category == "daily_limit" and records == []


def test_daily_quota_429_is_not_retryable():
    err = classify_status(429, "Quota exceeded", {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]})
    assert err.category == "quota_exhausted" and not err.retryable
    assert classify_status(429, "slow down", {"retryDelay": "7s"}).retry_after == 7.0
    assert classify_status(503, "high demand").retryable


def test_cost_uses_token_counts_and_bills_thinking_as_output(settings_override):
    settings_override(price_input_per_mtok=1.0, price_output_per_mtok=2.0)
    assert float(cost_of(1_000_000, 500_000, 500_000)) == pytest.approx(3.0)


def _plan(*subs):
    return Plan.model_validate({"brief": "a brief", "sub_questions": [
        {"id": i, "ask": f"question {i}", "depends_on": d} for i, d in subs]})


def test_plan_validation_accepts_dag_and_maps_dependencies_to_task_ids():
    plan = validate_plan(_plan(("q1", []), ("q2", []), ("q3", ["q1", "q2"])), 5)
    tasks = plan_to_tasks(plan)
    assert set(tasks[2]["depends_on"]) == {str(tasks[0]["id"]), str(tasks[1]["id"])}


@pytest.mark.parametrize("subs,message", [
    ((("q1", ["q2"]), ("q2", ["q1"])), "cycle"),
    ((("q1", ["q9"]),), "unknown"),
    ((("q1", ["q1"]),), "itself"),
    ((("q1", []), ("q1", [])), "unique"),
    ((("q1", []), ("q2", []), ("q3", [])), "limit"),
])
def test_plan_validation_rejects_bad_plans(subs, message):
    with pytest.raises(PlanError, match=message):
        validate_plan(_plan(*subs), 2)


def test_verdict_rule():
    s = lambda origin, score=0.9, found=True: EvidenceView("supports", score, found, origin)  # noqa: E731
    c = lambda origin, score=0.9: EvidenceView("contradicts", score, True, origin)  # noqa: E731
    assert decide_verdict([s("a.com"), s("b.org")], 2, 0.7)[0] == "supported"
    assert decide_verdict([s("a.com"), s("a.com")], 2, 0.7)[0] == "unverified"  # one origin twice
    assert decide_verdict([s("a.com"), s("b.org", found=False)], 2, 0.7)[0] == "unverified"  # invented quote
    assert decide_verdict([s("a.com"), s("b.org", score=0.3)], 2, 0.7)[0] == "unverified"  # weak judge
    assert decide_verdict([s("a.com"), s("b.org"), c("c.net")], 2, 0.7)[0] == "disputed"
    assert decide_verdict([s("a.com"), s("b.org"), c("c.net", 0.5)], 2, 0.7)[0] == "supported"  # weak contradiction
    assert decide_verdict([], 2, 0.7)[0] == "unverified"


def test_origin_map_merges_copies_across_domains():
    def src(domain, h, sh):
        return SimpleNamespace(id=uuid.uuid4(), domain=domain, content_hash=h, shingle_hash=sh)
    a, mirror, b = src("a.com", "h1", "s1"), src("mirror.com", "h2", "s1"), src("b.org", "h3", "s3")
    origins = origin_map([a, mirror, b])
    assert origins[a.id] == origins[mirror.id] != origins[b.id]


def test_registrable_domain_and_fingerprint():
    assert registrable_domain("https://www.news.bbc.co.uk/x") == "bbc.co.uk"
    assert registrable_domain("https://en.wikipedia.org/wiki/X") == "wikipedia.org"
    text = "The quick brown fox jumps over the lazy dog near the old river bank today. " * 3
    assert shingle_fingerprint(text) == shingle_fingerprint(text.upper().replace("  ", " "))


def test_quote_check_is_verbatim_but_whitespace_and_typography_tolerant():
    page = "The tower’s height is 330 metres.\nIt opened in 1889."
    assert quote_in_text("The tower's height is 330 metres.", page)
    assert not quote_in_text("The tower's height is 300 metres.", page)
    assert not quote_in_text("short", page)


def test_best_sentence_finds_matching_sentence():
    text = "Paris has many museums and parks to visit. The tower is 330 metres tall including antennas."
    sentence, overlap = best_sentence("The Eiffel Tower is 330 metres tall including its antennas", text)
    assert "330 metres" in sentence and overlap >= 0.6


def test_extract_page_text_title_and_date():
    html = ('<html><head><title>T</title><meta property="article:published_time" content="2025-02-03T10:00:00Z">'
            "<script>evil()</script></head><body><nav>menu menu menu menu menu menu</nav><article>"
            "<p>This is the first paragraph of the article body text.</p><p>Second paragraph with more words in it.</p>"
            "</article></body></html>")
    title, text, published = extract_page(html)
    assert title == "T" and str(published) == "2025-02-03"
    assert "first paragraph" in text and "evil" not in text and "menu" not in text


@pytest.mark.parametrize("url", ["http://127.0.0.1/admin", "http://localhost:8020/api", "file:///etc/passwd",
                                 "http://169.254.169.254/latest/meta-data", "ftp://example.com/x"])
def test_fetch_refuses_internal_and_non_http_urls(url):
    with pytest.raises(ToolError):
        asyncio.run(check_url_allowed(url))


def test_citation_check_drops_uncited_and_flags_misplaced_disputes():
    claims = {"C1": SimpleNamespace(id=uuid.uuid4(), verdict="supported"),
              "C2": SimpleNamespace(id=uuid.uuid4(), verdict="disputed"),
              "C3": SimpleNamespace(id=uuid.uuid4(), verdict="unverified")}
    output = WriterOutput.model_validate({"title": "Title", "sections": [
        {"heading": "Answer", "sentences": [
            {"text": "Fact.", "claim_ids": ["C1"]}, {"text": "Made up.", "claim_ids": []},
            {"text": "Ghost.", "claim_ids": ["C42"]}, {"text": "Contested.", "claim_ids": ["C2"]},
            {"text": "One source says.", "claim_ids": ["C3"]}]},
        {"heading": "Disputed or uncertain", "sentences": [{"text": "Contested.", "claim_ids": ["C2"]}]}]})
    sections, stats = citation_check(output, claims)
    assert stats["sentences_total"] == 6 and stats["sentences_kept"] == 4
    assert stats["sentences_dropped_uncited"] == 2 and stats["invalid_claim_ids"] == 1
    assert stats["disputed_cited_outside_disputed_section"] == 1
    assert stats["sentences_on_unverified_only"] == 1
    assert [s["idx"] for sec in sections for s in sec["sentences"]] == [0, 1, 2, 3]


def test_provider_schema_is_flat_and_keeps_title_property():
    import json as _json

    from app.llm.gateway import provider_schema

    schema = provider_schema(WriterOutput)
    dumped = _json.dumps(schema)
    assert "$ref" not in dumped and "$defs" not in dumped
    assert not any(k in dumped for k in ('"minLength"', '"maxLength"', '"minItems"', '"maxItems"'))
    assert "title" in schema["properties"] and schema["required"] == ["title", "sections"]


def test_cross_check_matches_differently_worded_sentence_with_same_number():
    # Real case from a live run: three sites state 828 m, worded differently from the extracted claim.
    claim = ("The architectural height of the Burj Khalifa (excluding the antenna, but including a 242.6 m spire) "
             "is 828 meters (2,717 feet).")
    page = ("The building was developed by Emaar. The Burj Khalifa is 828 m (2,717 ft) tall and has 163 floors. "
            "The figure is architectural height, measured from the entrance level to the top of the spire.")
    sentence, _ = best_sentence(claim, page)
    assert sentence.startswith("The Burj Khalifa is 828 m")
    # an unrelated sentence that only shares a number is still not a candidate
    assert best_sentence(claim, "The company reported 828 new customers in the quarter this year.") is None


def test_gateway_skips_a_model_at_its_daily_cap():
    first, second = _queue(OK, model="m1"), _queue(OK, model="m2")
    gateway, records = _gateway(first, second)
    gateway.allow_model = lambda model: model != "m1"
    result = asyncio.run(gateway.generate(PROMPT, VerifierOutput))
    assert result.model == "m2" and [r.model for r in records] == ["m2"]
    gateway.allow_model = lambda model: False
    with pytest.raises(ModelCallFailed) as exc:
        asyncio.run(gateway.generate(PROMPT, VerifierOutput))
    assert exc.value.category == "daily_limit"


def test_gateway_enforces_the_per_run_call_budget():
    gateway, records = _gateway(_queue(OK))
    gateway.allow_run_call = lambda run_id: False
    with pytest.raises(ModelCallFailed) as exc:
        asyncio.run(gateway.generate(PROMPT, VerifierOutput, CallContext(run_id=str(uuid.uuid4()))))
    assert exc.value.category == "run_call_budget" and records == []


def test_aggregate_counts_missing_reports_as_wrong():
    from app.evaluation import aggregate

    base = dict(cited_claims=0, cited_quote_backed=0, cited_supported_verdict=0, extracted_quotes=0,
                extracted_quotes_verbatim=0, domains=1, cost_usd=0.0, tokens=0, model_calls=1, wall_seconds=1.0)
    scores = [dict(base, has_report=True, answer_correct=True, fact_recall=1.0),
              dict(base, has_report=False, answer_correct=False, fact_recall=0.5)]
    agg = aggregate(scores)
    assert agg["answer_accuracy"] == 0.5 and agg["fact_recall_mean"] == 0.5


def test_gateway_falls_back_when_a_model_keeps_failing_with_server_errors():
    busy = ProviderError("server_error", "503 high demand", retryable=True)
    first = _queue(busy, busy, busy, model="m1")
    second = _queue(OK, model="m2")
    gateway, records = _gateway(first, second)
    result = asyncio.run(gateway.generate(PROMPT, VerifierOutput))
    assert result.model == "m2"
    assert [(r.model, r.status) for r in records] == [("m1", "error")] * 3 + [("m2", "success")]
