# Deployment

**Status: not deployed.** Everything below has been run locally: Docker Compose for PostgreSQL, Alembic
migrations up, down and up again, the API on port 8020 and the Vite app on port 5183. The production image
(`backend/Dockerfile`) was also built and run against the Compose database; it applied the migrations and
answered `/api/health` with `"database": "ok"`. The hosted setup
(Render + Neon + Vercel) is written out step by step but has **not** been carried out. There is no live URL.

| Piece | Target | Notes |
|---|---|---|
| API + orchestrator | Render web service (Docker, `backend/Dockerfile`) | one instance; the scheduler runs inside the API process |
| Database | Neon PostgreSQL | any Postgres 14+; the URL is normalised to `postgresql+psycopg://` |
| Frontend | Vercel (`frontend/`, Vite preset) | `vercel.json` adds the SPA rewrite and security headers |

## 1. Database (Neon)

1. Create a project and copy the pooled connection string.
2. Nothing else: `start.sh` runs `alembic upgrade head` on every boot, and fails fast if the database is unreachable.

## 2. API (Render)

Create a **Web Service** from the repository with **Root directory** `backend` and **Runtime** Docker.
Render sets `PORT`; `start.sh` reads it. Health check path: `/api/health`.

Environment variables (Render "Environment" tab; never in the repo):

| Variable | Value |
|---|---|
| `APP_ENV` | `production` (JSON logs, `/docs` disabled) |
| `DATABASE_URL` | the Neon connection string |
| `GEMINI_API_KEY` | server-side only |
| `GEMINI_MODELS` | `gemini-3.5-flash-lite,gemini-3.1-flash-lite` (ordered fallback) |
| `TAVILY_API_KEY` | strongly recommended: without it search falls back to Wikipedia only, and nothing can be "supported" by two independent domains |
| `FRONTEND_ORIGIN` | the Vercel URL, e.g. `https://deep-research.vercel.app` (comma-separate several) |
| `DEMO_ACCESS_CODE` | set this on a public demo so strangers cannot spend your quota |
| `DAILY_MODEL_CALL_LIMIT` | e.g. `150`: a hard stop below the provider quota |
| `RUNS_PER_HOUR_PER_CLIENT` | e.g. `5` |
| `HTTP_USER_AGENT` | a User-Agent with a contact URL (Wikimedia rejects anonymous agents) |

Things to know about this setup:

- **Single instance.** The scheduler is an asyncio loop inside the API process. Two instances would both
  resume the same runs. To scale out, add a lease column (or `SELECT … FOR UPDATE SKIP LOCKED`) so each run
  is claimed by one worker. See `backend/app/research/scheduler.py`.
- **Restarts are safe.** All progress lives in Postgres. On boot `resume_active_runs()` restarts any run that
  was planning, researching, verifying or writing: finished tasks are kept and interrupted ones run again.
  Render's free tier sleeps when idle, and a sleeping service resumes its runs when it wakes.
- **SSE through proxies.** `/api/runs/{id}/events` sends `X-Accel-Buffering: no` and a heartbeat comment
  every 15 s, and the browser reconnects with `Last-Event-ID`. The UI also polls every 5 s while a run is active,
  so a dropped stream never freezes the page.
- **Access code and SSE.** `EventSource` cannot send headers, so the stream accepts `?access_code=`; every
  other request sends `X-Access-Code`.

Local check of the production image:

```bash
docker compose --profile app up --build
```

That builds `backend/Dockerfile` and serves the API on http://localhost:8020 against the Compose database.

## 3. Frontend (Vercel)

Import the repository and set **Root directory** `frontend` (framework preset Vite), plus one variable:

| Variable | Value |
|---|---|
| `VITE_API_BASE_URL` | the Render URL, e.g. `https://deep-research-api.onrender.com` |

Only public values belong in `VITE_*` variables. The model and search keys stay on the server.

## 4. After deploying

1. `GET https://<api>/api/health` should report `"database": "ok"`, `"model_configured": true` and the expected `search_provider`.
2. Run one **quick** question end to end. Each run costs about 4–6 model calls, so keep a spend cap on the provider (roadmap: "guard the wallet").
3. Open **Cost & quality** to confirm the run's calls, tokens and cost were logged.

## Rollback

Redeploy the previous Render deploy. The migration history has one revision (`0001`). `alembic downgrade base`
drops everything, so take a Neon branch or snapshot first.
