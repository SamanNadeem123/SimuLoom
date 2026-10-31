# SimuLoom — Fairness-Aware Queue Experimentation Lab

SimuLoom simulates a university service centre (counters, visitors from several groups, impatience, no-shows) and
lets you compare **scheduling policies on identical, seeded workloads**. After each run it evaluates user-defined
**fairness constraints** (wait-time disparity between groups, SLA gaps, long-wait rates, maximum individual wait),
aggregates results over replications with confidence intervals, and states honestly when the evidence is insufficient.

Intended users: students, researchers and engineers exploring queue-scheduling fairness trade-offs.
Licence: **undecided** — no licence file is included.

## What is implemented
- **Simulation core** (SimPy 4.1.2): deterministic "settle-dispatcher" engine; events only record, one settle step per
  timestamp applies completions → abandonments → arrivals → dispatch → capacity blocking. Workloads are pre-generated per
  replication from `SeedSequence(base_seed, spawn_key=(replication, stream))` and fingerprinted (SHA-256), so every policy
  sees the same visitors. Policies never see service or patience times.
- **Policies**: `fcfs`; `static_priority` (lower class number first; can starve); `aging_priority` (`p_eff = class − r·wait`);
  `simuloom_fair` v0.1.0 (score `α·wait/T_ref + β·priority + γ·group-pressure`, plus an overdue-guard tier).
  `simuloom_fair` has a *provable* per-decision bound on overtaking; it does **not** guarantee any aggregate constraint.
  Its default weights are engineering assumptions, not tuned results.
- **Metrics**: waits (mean/median/p95, started-only and censored-inclusive), SLA attainment, long-wait rate, abandonment,
  blocking, throughput, utilization, mean/peak queue length, per-group versions with minimum-sample rules.
- **Constraints**: six constraint types; per run `SATISFIED` / `VIOLATED` / `NOT_EVALUABLE` (with reason codes such as
  `INSUFFICIENT_SAMPLE`, `EXCESSIVE_CENSORING`); across replications a Student-t CI on the mean (needs ≥ 5 valid
  replications and ≥ 80 % valid) gives `CONFIRMED` / `INCONCLUSIVE` certainty, or an all-replications rule for max individual wait.
  Thresholds are labelled assumptions.
- **API** (FastAPI + Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL): scenarios (versioned, duplicate, delete), validation,
  demo scenarios, experiment submit/status/results/replications/comparison/constraints/timeseries/export/retry/delete,
  `Idempotency-Key`, structured errors, 429 when more than 5 experiments are pending, request ids.
- **Execution**: PostgreSQL rows are the job queue. A supervisor thread claims `QUEUED` experiments (compare-and-set +
  lease + heartbeat) and runs each in one `spawn`ed child process; a sweeper fails experiments whose lease expired or that
  exceed the timeout; startup recovers orphaned runs. Status changes are audited in `experiment_status_event`.
- **Reproducibility/reporting**: immutable scenario snapshot + config hash stored per experiment, seeds, workload
  fingerprints per replication, CSV/JSON exports (summary, metrics, constraints), a JSON manifest and a Markdown report
  built only from stored results. PDF export is **not** implemented.
- **Frontend** (React 18 + TypeScript + Vite + Recharts): scenario editor with live validation, policy selection and
  parameters, run/status polling, overview dashboards, fairness analysis with constraint explanations, policy comparison,
  reproducibility tab, loading/empty/error/failed states. All numbers come from the API.

## Architecture
```
React UI ──/api──▶ FastAPI ──▶ PostgreSQL (scenarios, experiments, results, status events = job queue)
                      │                ▲
                      └─ supervisor ──▶ spawn child process: workload → SimPy engine → metrics → constraints → write results
```
Layout: `backend/src/simuloom/{domain,services,api,db,execution}`, `backend/alembic`, `backend/tests`, `backend/spikes`
(SimPy spike evidence in `spikes/REPORT.md`), `frontend/src`, `frontend/e2e`, `docker-compose.yml`, `.github/workflows/ci.yml`.

## Run with Docker Desktop (Windows, macOS, Linux)
Prerequisite: Docker Desktop running. In PowerShell, from the project folder:
```powershell
Copy-Item .env.example .env          # then open .env and set POSTGRES_PASSWORD to your own value
docker compose up -d --build --wait  # builds, starts PostgreSQL, runs migrations, starts API + UI
```
Open http://localhost:8080 (UI) · http://localhost:8000/api/docs (API docs) · http://localhost:8000/health.
```powershell
docker compose logs -f backend       # logs
docker compose down                  # stop (keeps data)
docker compose down -v               # stop and delete the database volume
```
Migrations run automatically at backend start (`alembic upgrade head`). Ports are bound to 127.0.0.1 only.

## Run an experiment
1. **Scenarios** → *Create from template* (or edit your own); fix any validation messages; save.
2. **Run an experiment**: choose policies (the same workloads are used for all), base seed, replications; submit.
3. Watch status; then read **Overview**, **Fairness** (per-constraint status, certainty, magnitude), **Policy comparison**
   (paired differences), **Reproducibility** (seed, config hash, fingerprints, manifest/report/CSV downloads).
Interpretation: `NOT_EVALUABLE` means the data were insufficient — it is not a pass. `INCONCLUSIVE` means the point
estimate is on one side of the threshold but the confidence interval crosses it.

## Local development (no Docker)
Backend (needs Python ≥ 3.12, [uv](https://docs.astral.sh/uv/), PostgreSQL 16):
```
cd backend
uv sync
export SIMULOOM_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/simuloom
uv run alembic upgrade head
uv run uvicorn simuloom.api.app:create_app --factory --port 8000
```
Frontend (Node 22): `cd frontend && npm ci && npm run dev` (proxies `/api` to `http://localhost:8000`).

## Tests and checks
```
cd backend
export SIMULOOM_TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/simuloom_test  # WARNING: schema is dropped
uv run pytest -q                       # database tests are skipped when the variable is unset
uv run ruff check src tests spikes alembic && uv run ruff format --check src tests spikes alembic
uv run mypy
cd ../frontend
npm run typecheck && npm run lint && npm test && npm run build
E2E_BASE_URL=http://localhost:8080 npm run e2e    # needs the running stack and Playwright Chromium
```
Verified results for this release are listed in [VERIFICATION.md](VERIFICATION.md).

## API summary (`/api/v1`, interactive docs at `/api/docs`)
`GET /metadata`, `GET /policies`, `POST|GET /scenarios`, `GET|PATCH|DELETE /scenarios/{id}`, `POST /scenarios/{id}/duplicate`,
`GET /scenarios/{id}/versions`, `POST /scenario-validations`, `GET /demo-scenarios`, `POST /demo-scenarios/{key}/instantiate`,
`POST|GET /experiments` (`Idempotency-Key` supported), `GET /experiments/{id}`, `/results`, `/replications`, `/comparison`,
`/constraints`, `/timeseries`, `/export?kind=summary|metrics|constraints|manifest|report&format=csv|json|md`,
`POST /experiments/{id}/retry`, `DELETE /experiments/{id}`, `GET /experiment-comparisons`; `GET /health` (outside the prefix).
Errors use one JSON shape with a code, message and per-field details.

## Known limitations
- Docker images and `docker compose` were **not built or run** in the development environment (no Docker daemon);
  the Dockerfiles/Compose file were only validated with `docker compose config` and a manual replay of the install,
  migration and import steps. The compose CI job will exercise them on GitHub.
- No authentication/authorization: intended for local or trusted-network use. The API runs a single supervisor (one API process).
- Fair-policy weights and constraint-evaluation thresholds are engineering assumptions; fairness outcomes are
  simulation results for the modelled scenario, not claims about real services.
- Experiments are bounded (≤ 20 000 expected visitors, ≤ 30 replications, ≤ 50 counters, ≤ 8 groups); no PDF export;
  no cancellation of a running experiment (timeout and delete only); scenario "archive" is delete.
