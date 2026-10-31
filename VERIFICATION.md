# Verification (run against the final code, 2026-10-28, Linux, Python 3.13, Node 22, PostgreSQL 16 started locally)

| Check | Command | Result |
|---|---|---|
| Backend tests (incl. PostgreSQL API/persistence/recovery tests) | `uv run pytest -q` with `SIMULOOM_TEST_DATABASE_URL` set | 175 passed |
| Lint / format | `uv run ruff check src tests spikes alembic`; `uv run ruff format --check ...` | all checks passed; 66 files formatted |
| Types | `uv run mypy` (strict) | no issues in 46 source files |
| Migrations | `alembic upgrade head` on empty databases (test fixture and a fresh dev DB) | applied |
| Frontend types / lint / unit tests | `npm run typecheck`, `npm run lint`, `npx vitest run` | clean; 20 tests passed (3 files) |
| Frontend production build | `npm run build` | built |
| End-to-end (real backend + real PostgreSQL + built UI via `vite preview`, Chromium) | `npm run e2e` | 1 passed: template scenario → run experiment → fairness/comparison/reproducibility tabs → manifest download |
| Docker | `docker compose config` with a password set; install/migrate/import steps of the backend Dockerfile replayed manually | config valid; **images were not built and Compose was not run (no Docker daemon available)** |

Backend DB tests are skipped (not failed) when `SIMULOOM_TEST_DATABASE_URL` is unset: 149 passed, 26 skipped in that mode.
