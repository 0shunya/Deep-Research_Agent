# Deep Research Agent

**Ask a hard question. A planner splits it into sub-questions you can edit, parallel researcher agents
search and read the web, a verifier checks every claim against the exact quotes behind it, and a writer
produces a report in which every sentence links to its evidence.**

For anyone who needs a cited answer they can audit, not a confident paragraph. Build 04 of the
"AI Engineer Projects Roadmap": planning, agent orchestration, source verification.

- **Live link:** none yet. It runs locally end to end. Deployment steps for Render, Neon and Vercel are in [DEPLOYMENT.md](DEPLOYMENT.md) but have not been carried out.
- **Demo video:** not recorded yet.
- **Headline number (live, n=10):** 87.5% of cited claims are backed by a verbatim quote the verifier judged
  as supporting. 0% reached the strict "supported" verdict, because the keyless Wikipedia search gives only
  one independent origin (see [Evaluation](#evaluation)).


## What it does

1. **Ask** a question with a depth (quick, standard or deep), a token budget and a number of parallel researchers.
2. **Review the plan.** The planner returns JSON (a brief plus sub-questions with `depends_on`). You can
   edit, add or remove sub-questions and rewire dependencies; cycles are rejected. Nothing is searched until you approve.
3. **Watch it work.** Server-sent events stream each search, page read, claim extracted and claim checked. Independent sub-questions run at the same time.
4. **Read the report.** Every sentence carries numbered claim chips coloured by verdict. Disputed claims appear in their own section. Single-source claims are hedged.
5. **Audit a claim.** Each quote is shown with its stance (supports, contradicts or not enough) and confidence, highlighted in the stored page text, with the source's domain, publish and fetch dates, and content hash.

## Architecture

```mermaid
flowchart LR
  U[React UI] -->|REST + SSE| API[FastAPI]
  API --> S[Scheduler<br/>asyncio, state in Postgres]
  S --> P[Planner] --> O[Orchestrator<br/>task DAG + caps]
  O --> R1[Researcher] & R2[Researcher] & R3[Researcher]
  R1 & R2 & R3 --> W[Search: Tavily or Wikipedia<br/>Fetch: httpx + SSRF guard]
  O --> V[Verifier<br/>verbatim → cross-check → LLM judge → verdict rule]
  V --> WR[Writer + citation check]
  P & R1 & V & WR --> G[Model gateway] --> M((Gemini))
  G --> MC[(model_calls)]
  O --> DB[(runs · tasks · sources · claims · evidence · reports)]
```

| Layer | Choice |
|---|---|
| API | FastAPI, Pydantic v2, SSE progress tailing a `run_events` table |
| Orchestration | plain asyncio: a DAG of tasks with caps on workers, steps, attempts, tokens and wall time; resumes after a crash |
| Model | Gemini (`gemini-3.5-flash-lite`, falling back to `gemini-3.1-flash-lite`) through one gateway that logs every call |
| Search / fetch | Tavily when `TAVILY_API_KEY` is set, otherwise the Wikipedia REST API; `httpx` plus a readability-style extractor |
| Data | PostgreSQL 16, SQLAlchemy 2, Alembic |
| UI | React 19, Vite, TypeScript, Tailwind v4, TanStack Query, React Router |

The verdict rule (a design choice, as in the roadmap): **supported** = verbatim quotes from 2 or more
independent origins and no strong contradiction; **disputed** = a strong contradiction; otherwise
**unverified**. Sources that share a domain, a content hash or a near-duplicate fingerprint count as one
origin, so ten copies of one press release count once.

Full walkthrough with file paths: [WORKFLOW.md](WORKFLOW.md). Design questions answered from the code: [INTERVIEW_QUESTIONS.md](INTERVIEW_QUESTIONS.md).

## Evaluation

Rule from the roadmap: no eval, not done. Mocked tests and live-model runs are kept strictly apart below.

### Live: real Gemini plus real search (`evaluation/results/live-wiki-quick.json`)

Measured on 2026-10-31 with `gemini-3.5-flash-lite`, **Wikipedia search** (no Tavily key yet), quick depth,
3 workers. **10 of the 22 labelled questions** ran, because the free Gemini quota is shared with another
project. The runner is resumable, so the rest can follow. Raw records, including every quote and model call,
are in `evaluation/runs/live-wiki-quick/`.

| Metric | Result | How it is measured |
|---|---|---|
| Answer accuracy | **9 / 10** | an accepted answer string appears in the report (q07 omitted "Los Altos") |
| Required-fact recall | **0.85** (mean) | share of each question's required facts present in the report |
| Cited claims backed by a verbatim supporting quote | **87.5%** (14 / 16) | quote found in the stored page **and** judged "supports" |
| Cited claims with the "supported" verdict | **0%** (0 / 16) | needs 2+ independent origins; Wikipedia is one origin |
| Researcher quotes found verbatim in the page | **100%** | deterministic substring check |
| Independent domains per report | 1 (median) | Wikipedia only |
| Model calls per report | 4.9 (mean) | plan + 1–2 research + verify + write, including retries |
| Tokens per report | 6,487 (mean) | real usage metadata |
| Cost per report | **$0.0008** (mean) | tokens × configured list price ($0.10 / $0.40 per 1M in/out); the free tier billed $0. Check current Gemini pricing before quoting it |
| Wall time per report | p50 10.5 s, max 32.1 s | research start to report |

Answer and fact checks are deterministic string matches. They are cheap and reproducible, but a correct
paraphrase can be scored as wrong, so treat accuracy as a lower bound. With n=10 these are indicative numbers, not benchmarks.

Cost and latency across **all 13 completed live runs** (the eval runs plus UI runs, from the in-app dashboard
over `model_calls`): average cost $0.00081 and 6.3k tokens per report; wall time p50 14.8 s, p95 32.4 s.
Per-stage p50 latency: plan 1.7 s, research 1.8 s, verify 1.3 s, write 1.2 s. 3 of 63 model calls errored:
2 writer 400s before the schema fix, and 1 rate limit that was retried successfully.

### Live: verifier on labelled claim–quote pairs (`evaluation/results/verifier-live.json`)

24 hand-written pairs, 8 each of supports, contradicts and insufficient, including one prompt-injection quote,
judged in one batched call: **24 / 24 correct, 0 false "supports"**. The set is small and easy. It shows the
judge behaves sensibly; it does not show robustness.

### Parallel workers

| Setting | 1 worker | 2 workers | 4 workers |
|---|---|---|---|
| **Simulated**: 4 independent sub-questions, real orchestrator, stage latencies injected at the live p50s | 16.6 s | 10.4 s | 7.4 s (2.2×) |

Live, at quick depth (the planner made 1–2 sub-questions): q20 took 14.2 s with 1 worker and 8.8 s with 3, while
q07 took 8.9 s and 9.4 s. With n=2 and so little to parallelise, the live comparison is **inconclusive**. The
simulated benchmark is where the speedup is measured; it does not measure Gemini or the web.

### Mocked test suites (no network, no quota)

| Suite | Result |
|---|---|
| `pytest` (unit + orchestration + API), SQLite | **42 passed** |
| same suites on PostgreSQL 16 | **42 passed** |
| Vitest (frontend) | **6 passed** |
| `ruff` (F, E9, B), `tsc`, `oxlint`, `vite build` | clean |

These cover the claims the roadmap's milestones make:
- **M4:** a planted false claim with a fabricated quote ends up unverified.
- **M5:** a sentence citing a made-up claim id is dropped.
- **Durability and caps:** budget stop, retries, cancel that keeps partial results, resume after a crash.

## Run it locally (three commands, plus keys)

Prerequisites: Docker, Python 3.12+ (developed on 3.14), Node 20+ (developed on 24).

```bash
cp backend/.env.example backend/.env   # then set GEMINI_API_KEY (and TAVILY_API_KEY for multi-domain search)
docker compose up -d db && cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt && .venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --port 8020   # in another terminal: cd frontend && npm install && npm run dev
```

(On macOS and Linux use `.venv/bin/`.) Open http://localhost:5183. To click through a finished report
without any key or network, seed the offline demo: `cd backend && .venv/Scripts/python -m scripts.seed_demo`
(scripted content, labelled "[offline demo]", and excluded from the cost dashboard).

Ports: API 8020, web 5183, PostgreSQL 5436 (Compose project `deep-research-agent`).

Tests and evaluation:

```bash
cd backend && .venv/Scripts/python -m pytest
```

```bash
cd frontend && npm test
```

```bash
backend/.venv/Scripts/python evaluation/run_eval.py --name live-wiki-quick --ids q03,q04 --depth quick
```

```bash
backend/.venv/Scripts/python evaluation/eval_verifier.py --name verifier-live
```

```bash
backend/.venv/Scripts/python evaluation/bench_parallel.py
```

## Decisions and trade-offs

- **Plain asyncio, not LangGraph or a queue.** The control flow is a small DAG loop, and keeping it in my
  own code keeps it explainable. Durability comes from Postgres rows plus resume-on-boot. The cost is that
  only one API instance can run; scaling out needs lease-based claiming.
- **Verbatim quotes before any LLM judging.** It is cheap, it cannot be argued with, and it catches a
  fabricated quote without spending tokens. The LLM judge only sees quotes that really exist.
- **A strict two-origin rule.** It makes the "supported" share honest but harsh with a single-publisher
  search provider, as the 0% above shows. The fix is better search (Tavily), not a looser rule.
- **The writer sees claims, not pages, and a deterministic citation check runs last.** A sentence with no
  real claim behind it is dropped, not trusted.
- **One gateway.** It caught a real provider quirk: Gemini rejected the writer's schema because of its nested
  size limits. Now the provider gets a flat schema and Pydantic enforces the limits.

What failed, and what I measured from it:
- Wikipedia returned 403 to an anonymous User-Agent, and the run was wrongly marked "completed". Now such runs fail with `research_failed`, and the User-Agent is configurable.
- Two parallel researchers storing the same URL raced on a unique constraint (caught by an intermittent test). Fixed.
- Search sometimes returns the wrong entity (a Liverpool "Shanghai Tower" proposal). At quick depth there is no follow-up search to recover.

Next, with more time:
1. Add a Tavily key and re-run all 22 questions at standard depth.
2. Answer grading that tolerates paraphrase.
3. Recency weighting using the stored publish dates.
4. Lease-based multi-instance workers.
5. A harder verifier set built from real disagreements.

## Repository

```
backend/app/llm/          gateway, provider adapter, versioned prompts, output schemas
backend/app/research/     planning, orchestrator, researcher, web, verifier, writer, scheduler, simulated
backend/app/api/          routes (REST + SSE), schemas, errors, rate limit
backend/migrations/       Alembic (0001 initial schema)
backend/tests/            unit, orchestration and API tests
evaluation/               labelled questions, verifier pairs, runners, raw runs, results
frontend/src/             pages, components, API client, SSE hook, tests
```
