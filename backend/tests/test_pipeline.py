"""End-to-end orchestration with the scripted model and the simulated web (no network, no quota)."""

import asyncio

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.llm.schemas import Plan
from app.models import Claim, Evidence, ModelCall, Report, ReportCitation, Run, RunEvent, Source, Task
from app.research.orchestrator import approve_plan, execute_run, plan_run


def _plan_and_approve(env, run_id, edit=None):
    asyncio.run(plan_run(env, run_id))
    with session_scope() as db:
        run = db.get(Run, run_id)
        assert run.status == "awaiting_approval", run.error_message
        plan = Plan.model_validate(edit(run.plan) if edit else run.plan)
        approve_plan(db, run, plan)


def test_full_run_produces_verified_cited_report(make_env, new_run):
    env = make_env()
    run_id = new_run()
    _plan_and_approve(env, run_id)
    asyncio.run(execute_run(env, run_id))
    with session_scope() as db:
        run = db.get(Run, run_id)
        assert run.status == "completed", run.error_message
        assert all(t.status == "done" for t in run.tasks), [(t.key, t.status, t.error) for t in run.tasks]
        # Each source fetched once per run, with hash and domain stored.
        sources = list(db.scalars(select(Source).where(Source.run_id == run_id)))
        assert {s.domain for s in sources} == {"alpha-news.com", "beta-journal.org", "gamma.net"}
        assert all(len(s.content_hash) == 64 and s.raw_content for s in sources)
        verdicts = {c.text: c.verdict for c in db.scalars(select(Claim).where(Claim.run_id == run_id))}
        # Completion date: confirmed by two independent domains.
        assert verdicts["The Eiffel Tower was completed in 1889 for the World's Fair in Paris"] == "supported"
        # Height: 300 m on one page, 330 m on another -> disputed, not hidden.
        assert verdicts["The Eiffel Tower is 300 metres tall including its antennas"] == "disputed"
        report = db.scalar(select(Report).where(Report.run_id == run_id))
        assert report.stats["writer"] == "model"
        assert report.stats["sentences_dropped_uncited"] == 1  # the made-up C999 citation
        assert report.stats["invalid_claim_ids"] == 1
        cited = db.scalar(select(func.count(ReportCitation.id)).where(ReportCitation.report_id == report.id))
        assert cited == report.stats["sentences_kept"] > 0
        assert "## Sources" in report.markdown and "[1]" in report.markdown
        calls = list(db.scalars(select(ModelCall).where(ModelCall.run_id == run_id)))
        assert {c.feature for c in calls} == {"plan", "research", "verify", "write"}
        assert run.spent_tokens == sum((c.input_tokens or 0) + (c.output_tokens or 0) for c in calls)
        types = [e.type for e in db.scalars(select(RunEvent).where(RunEvent.run_id == run_id))]
        assert "task_started" in types and "source_read" in types and "report_ready" in types


def test_planted_false_claim_is_never_supported(make_env, new_run):
    env = make_env(planted_claim="The Eiffel Tower was moved to London in 1999")
    run_id = new_run()
    _plan_and_approve(env, run_id)
    asyncio.run(execute_run(env, run_id))
    with session_scope() as db:
        planted = list(db.scalars(select(Claim).where(Claim.text.like("%London%"))))
        assert planted and all(c.verdict in ("unverified", "disputed") for c in planted)
        ev = db.scalars(select(Evidence).where(Evidence.claim_id == planted[0].id,
                                               Evidence.origin == "extracted")).first()
        assert ev.quote_found is False and ev.stance == "insufficient"


def test_dependencies_run_after_their_inputs(make_env, new_run):
    env = make_env(sub_questions=["When was it completed?", "How tall is it?", "Combine q1 and q2"])
    run_id = new_run()
    _plan_and_approve(env, run_id)
    asyncio.run(execute_run(env, run_id))
    with session_scope() as db:
        tasks = {t.key: t for t in db.scalars(select(Task).where(Task.run_id == run_id))}
        assert tasks["q3"].started_at >= max(tasks["q1"].finished_at, tasks["q2"].finished_at)


def test_edited_plan_with_cycle_is_rejected(make_env, new_run):
    from app.research.planning import PlanError

    env = make_env()
    run_id = new_run()

    def cyclic(plan):
        plan["sub_questions"][0]["depends_on"] = ["q2"]
        plan["sub_questions"][1]["depends_on"] = ["q1"]
        return plan

    with pytest.raises(PlanError):
        _plan_and_approve(env, run_id, edit=cyclic)


def test_budget_stops_research_but_still_reports(make_env, new_run, settings_override):
    # One worker, and research may only use half of a small budget: the first sub-question's model call
    # crosses that line, so the second is skipped and the run moves on to verify + write.
    settings_override(research_budget_share=0.5)
    env = make_env()
    run_id = new_run(budget=1_200, workers=1)
    _plan_and_approve(env, run_id)
    asyncio.run(execute_run(env, run_id))
    with session_scope() as db:
        run = db.get(Run, run_id)
        assert run.status == "completed", run.error_message
        assert run.stop_reason == "budget"
        assert sorted(t.status for t in run.tasks) == ["done", "skipped"]
        assert db.scalar(select(Report).where(Report.run_id == run_id)) is not None


def test_run_fails_when_nothing_could_be_researched(make_env, new_run, settings_override):
    settings_override(research_budget_share=0.0001)
    env = make_env()
    run_id = new_run(budget=5_000)
    _plan_and_approve(env, run_id)
    asyncio.run(execute_run(env, run_id))
    with session_scope() as db:
        run = db.get(Run, run_id)
        assert run.status == "failed" and run.error_code == "research_failed"


def test_worker_failure_is_retried_then_recorded(make_env, new_run):
    from app.llm.providers import ProviderError, ScriptedProvider
    from app.research.simulated import scripted_responder

    inner = scripted_responder()
    failed_once: set[str] = set()

    def flaky(request):
        # The first research call of every sub-question fails with a non-retryable model error, so the
        # orchestrator (not the gateway) must retry the task.
        if request.feature == "research":
            key = request.user_text.split("Sub-question (")[1].split(")")[0]
            if key not in failed_once:
                failed_once.add(key)
                return ProviderError("bad_request", "boom", retryable=False)
        return inner(request)

    env = make_env(provider=ScriptedProvider(flaky))
    run_id = new_run()
    _plan_and_approve(env, run_id)
    asyncio.run(execute_run(env, run_id))
    with session_scope() as db:
        run = db.get(Run, run_id)
        assert run.status == "completed"
        assert sorted(t.attempts for t in run.tasks) == [2, 2]
        assert all(t.status == "done" for t in run.tasks), [(t.key, t.status, t.error) for t in run.tasks]


def test_cancel_keeps_partial_results(make_env, new_run):
    from app.research.simulated import scripted_provider, static_web

    env = make_env(provider=scripted_provider(latency=0.3), web=static_web(latency=0.05))
    run_id = new_run()
    asyncio.run(plan_run(env, run_id))
    with session_scope() as db:
        run = db.get(Run, run_id)
        approve_plan(db, run, Plan.model_validate(run.plan))

    async def go():
        job = asyncio.create_task(execute_run(env, run_id))
        await asyncio.sleep(0.6)  # research is under way
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)

    asyncio.run(go())
    with session_scope() as db:
        run = db.get(Run, run_id)
        assert run.status == "cancelled" and run.stop_reason == "cancelled"
        assert db.scalar(select(func.count(Source.id)).where(Source.run_id == run_id)) > 0
        report = db.scalar(select(Report).where(Report.run_id == run_id))
        assert report is not None and report.stats["writer"] == "fallback"


def test_resume_after_crash_reruns_only_unfinished_tasks(make_env, new_run):
    env = make_env()
    run_id = new_run()
    _plan_and_approve(env, run_id)
    # Simulate a crash mid-run: q1 finished, q2 was "running" when the process died.
    with session_scope() as db:
        tasks = list(db.scalars(select(Task).where(Task.run_id == run_id).order_by(Task.position)))
        tasks[0].status, tasks[0].attempts, tasks[0].summary = "done", 1, "q1 already done"
        tasks[1].status, tasks[1].attempts = "running", 1
    asyncio.run(execute_run(env, run_id))
    with session_scope() as db:
        tasks = list(db.scalars(select(Task).where(Task.run_id == run_id).order_by(Task.position)))
        assert tasks[0].attempts == 1 and tasks[0].summary == "q1 already done"
        assert tasks[1].status == "done" and tasks[1].attempts == 2
        assert db.get(Run, run_id).status == "completed"
