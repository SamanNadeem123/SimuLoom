# Spike report: SimPy step-driven settle dispatcher (Phase 0 spec 4.6)

Environment: Linux, Python 3.13.16, SimPy 4.1.2 (pinned), uv-managed venv.
Command: `uv run pytest spikes -q` -> **13 passed** (SP-01..SP-10; SP-04 runs 4 policy stubs).

Findings
- SimPy source (`simpy/core.py`) confirms heap ordering `(time, priority, eid)` and that `run(until=t)` schedules its stop event URGENT (events at exactly `t` are not processed). Verified by SP-10.
- **Defect found in the spec 4.4 pseudo-loop:** a final settle after the loop exit could start services whose events were then never executed (SP-06/SP-09 failed). Fixed: loop re-evaluates `peek()` after every settle. Prototype and tests updated.
- Test correction: Resource grants for queued requests are later events (2 steps), not synchronous. Does not affect the design (grants are drained before settle because `peek() == now`).
- Arrivals are scheduled from t=0 with absolute delay so float timestamps are exact (SP-10, V-8).
- Mutation checks: moving completions after arrivals fails SP-03; moving expiry after dispatch fails SP-04 (fcfs, static) and SP-07.

Not verified: Windows, Python 3.12 (CI to be added in Phase 7). Spike code stays in `spikes/`, not shipped.
