# Deployment: Render (API) + Neon (Postgres) + Vercel (frontend)

**Status: not deployed yet.** There is no live URL. Everything here was run locally:

- Docker Compose for Postgres.
- Alembic `upgrade` / `downgrade` / `upgrade`, and `alembic check`.
- The production image (`backend/Dockerfile`), built and run against the Compose database; it applied the migrations and answered `/api/health`.
- The CI steps in `.github/workflows/ci.yml`.
- Two full live reports on the production model list.

The hosted steps below have not been carried out yet.

| Piece | Where | Config in the repo |
|---|---|---|
| API + background runs | Render web service, Docker, free plan | `render.yaml` (Blueprint), `backend/Dockerfile`, `backend/start.sh` |
| Database | Neon PostgreSQL | migrations run on every boot (`start.sh` → `alembic upgrade head`) |
| Frontend | Vercel, Vite preset, root `frontend/` | `frontend/vercel.json` (SPA rewrite, security headers) |

## Quota plan for the live demo

The Gemini key is a free-tier key, about 20 requests a day **per model**, and it is shared by three demos.
Each demo uses its own models so they never compete:

| App | Models |
|---|---|
| AI Expense Scanner | `gemini-3.5-flash-lite` |
| AI Phone Receptionist | its own model, chosen by that project |
| **Deep Research Agent** | `gemini-3.1-flash-lite-preview`, falling back to `gemini-3-flash-preview` |

Both of this app's models were tested with the key on 2026-10-31: two full reports ran on
`gemini-3.1-flash-lite-preview` (`evaluation/runs/live-tavily-prod-models/`). It is slower than flash-lite,
at 5–12 s per call, and once hung for 88 s before returning a 5xx. That is why production sets a 30 s timeout
and the gateway falls back to the second model after repeated server errors.

The limits in `render.yaml` are counted from this app's own `model_calls` table:

| Setting | Value | Effect |
|---|---|---|
| `DAILY_CALLS_PER_MODEL` | 15 | the gateway skips a model that has used 15 calls today (UTC) and moves to the next |
| `DAILY_MODEL_CALL_LIMIT` | 30 | hard stop for the whole app per day |
| `MAX_MODEL_CALLS_PER_RUN` | 10 | one report can never use more; research stops early and leaves 2 calls for verify and write |
| `RUNS_PER_HOUR_PER_CLIENT` | 3 | per-IP rate limit on starting runs |
| `DEMO_ACCESS_CODE` | (secret) | strangers cannot start runs at all |

**How many reports per day.** A quick report used 5.0 model calls on average across 22 live runs (4–7,
retries included). That gives **about 5 quick reports a day**, and at least 3 even if every run hits the
10-call cap. When fewer than 4 calls are left for the day, `POST /api/runs` returns `429 daily_quota_reached`
("resets at 00:00 UTC") instead of starting a run that would fail partway. The sidebar shows the calls left
(`/api/health → model_calls_left_today`). Keep the UI's default **quick** depth for the demo.

Search runs through Tavily on its own key and quota: about 2–4 searches per quick report, well inside Tavily's free monthly allowance.

## 1. Neon (database)

1. Create a project at neon.tech, in a region close to the Render region you will pick.
2. Copy the **pooled** connection string (`postgresql://…-pooler…/neondb?sslmode=require`). Any
   `postgres://` or `postgresql://` URL works; the app rewrites it to the psycopg driver.
3. Nothing else: the schema is created by `alembic upgrade head` when the API boots.

## 2. Render (API)

1. Push the repository to GitHub.
2. In Render: **New → Blueprint**, select the repository. Render reads `render.yaml` and proposes the
   `deep-research-agent-api` web service (Docker, root `backend`, free plan, health check `/api/health`).
3. Fill in the five secrets it asks for (`sync: false`):

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Neon pooled connection string |
   | `GEMINI_API_KEY` | the Gemini key |
   | `TAVILY_API_KEY` | the Tavily key (without it, search falls back to Wikipedia and nothing can reach "supported") |
   | `FRONTEND_ORIGIN` | the Vercel URL from step 3, e.g. `https://deep-research-agent.vercel.app`; comma-separate several, no trailing slash |
   | `DEMO_ACCESS_CODE` | a code you share with reviewers |

   You can set `FRONTEND_ORIGIN` to a placeholder first and update it after Vercel gives you the URL. Changing an env var redeploys.
4. Deploy. On boot, `start.sh` runs the migrations, then uvicorn on Render's `$PORT`.

**Restarts, deploys and the free plan's sleep.** A free instance sleeps after about 15 minutes without
traffic and gets SIGTERM on every deploy. All run state is in Postgres:
- **On shutdown**, `RunScheduler.stop()` cancels in-flight jobs. The orchestrator tells this apart from a user cancel by the `cancel_requested` flag: interrupted tasks go back to `pending`, and the run keeps its status.
- **On boot**, `resume_active_runs()` restarts every run that was planning, researching, verifying or writing. Finished tasks are never redone.
- **Covered by tests:** `test_shutdown_mid_run_leaves_it_resumable` and `test_unfinished_runs_resume_when_the_server_starts`.
- **In practice:** a quick report takes 10–50 s and its open SSE stream counts as traffic, so the instance does not sleep mid-run. If the process is killed hard, tasks left `running` are reset to `pending` on the next boot.
- **Single instance only:** the scheduler lives in the API process. Do not scale to two instances without adding lease-based run claiming.

**SSE through Render's proxy.** `GET /api/runs/{id}/events`:
- Sends `Content-Type: text/event-stream`, `Cache-Control: no-cache` and `X-Accel-Buffering: no` (asserted in `test_api.py`), plus a first byte (`retry: 2000`) immediately.
- Sends a heartbeat comment every 15 s, so idle proxies keep the connection open.
- Gives every event an `id:`, so `EventSource` resumes with `Last-Event-ID` after a reconnect. Events come from the `run_events` table, so nothing is lost across reconnects or restarts.
- Is not compressed (there is no gzip middleware, which would buffer the stream).

The UI also polls the run every 5 s while it is active, so even a dropped stream never freezes the page.
`EventSource` cannot send headers, so the stream accepts the access code as `?access_code=`.

## 3. Vercel (frontend)

1. **Add New → Project**, import the repository, set **Root Directory** to `frontend`. The Vite preset is detected (`npm run build` → `dist`).
2. Environment variable: `VITE_API_BASE_URL` = the Render URL, e.g. `https://deep-research-agent-api.onrender.com` (no trailing slash).
3. Deploy, then put the Vercel URL into Render's `FRONTEND_ORIGIN` (CORS).

Only public values go in `VITE_*` variables. The model and search keys stay on Render.

## 4. Smoke test (after each deploy)

The free instance may take about a minute to wake on the first request.

```bash
curl -s https://deep-research-agent-api.onrender.com/api/health
```

Expect `"status":"ok"`, `"database":"ok"`, `"model_configured":true`, `"search_provider":"tavily"`,
`"models":["gemini-3.1-flash-lite-preview","gemini-3-flash-preview"]`, `"access_code_required":true`, and a
`model_calls_left_today` number.

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://deep-research-agent-api.onrender.com/api/runs
```

Expect `401`: the access code is enforced.

Then in the browser, on the Vercel URL:
1. Enter the access code.
2. Start a **quick** question, for example "When did the James Webb Space Telescope launch, and at which Lagrange point does it operate?".
3. Approve the plan and watch the live activity stream. Events should arrive as they happen, not all at the end.
4. Open the report, click a claim chip, and check that quotes, sources and dates appear.
5. Open **Cost & quality** and confirm the run's 4–7 model calls were logged.

One smoke test uses about 5 of the day's 30 model calls.

## 5. CI

`.github/workflows/ci.yml` runs on every push and pull request:

- **Backend:** ruff; Alembic `upgrade head` + `alembic check` on an empty Postgres 16; the test suite on SQLite; the full suite on Postgres.
- **Frontend:** `npm ci`, oxlint, Vitest, `vite build`.

No test calls Gemini or the web: they use the scripted model and the offline web in `backend/app/research/simulated.py`.

## Rollback

Redeploy the previous successful deploy from the Render dashboard. The migration history has one revision
(`0001`); `alembic downgrade base` drops everything, so take a Neon branch first if you ever need it.
