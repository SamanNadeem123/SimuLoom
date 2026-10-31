# SimuLoom backend

See the project [README](../README.md). Quick commands (from `backend/`, requires uv and PostgreSQL for DB tests):
`uv sync`, `uv run alembic upgrade head`, `uv run pytest -q`, `uv run ruff check src tests spikes alembic`, `uv run mypy`.
Policies: `fcfs`, `static_priority`, `aging_priority` (`aging_rate_per_minute`), `simuloom_fair`
(`alpha, beta, gamma, p_max, t_ref_minutes, d_scale_minutes, w_guard_minutes, n_pressure_min`).
SimPy spike evidence: `spikes/REPORT.md`. Licence undecided; no licence file.
