"""Execution: child process, leases, heartbeats, timeout, crash recovery, retry, idempotent claiming."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from simuloom.db.base import session_scope
from simuloom.db.models import Experiment, PolicyRun, Replication, utcnow
from simuloom.execution.supervisor import Supervisor, claim_next, heartbeat, sweep_expired
from tests.conftest import make_settings
from tests.test_api import POLICIES, make_scenario, submit

pytestmark = pytest.mark.usefixtures("factory")


def exp_row(factory: sessionmaker[Session], eid: str) -> Experiment:
    with session_scope(factory) as s:
        row = s.execute(select(Experiment).where(Experiment.id == eid)).scalar_one()
        s.expunge(row)
        return row


def events(client: TestClient, eid: str) -> list[str]:
    return [e["to"] for e in client.get(f"/api/v1/experiments/{eid}?include_events=true").json()["status_events"]]


def queued(client: TestClient) -> str:
    return submit(client, make_scenario(client)["id"])["id"]  # type: ignore[no-any-return]


def test_child_process_runs_experiment_and_completes(client: TestClient, factory: sessionmaker[Session]) -> None:
    eid = queued(client)
    assert Supervisor(factory, make_settings()).run_one() is True
    row = exp_row(factory, eid)
    assert row.status == "COMPLETED" and row.replications_done == 6 and row.lease_owner is None and row.finished_at
    assert events(client, eid) == ["QUEUED", "RUNNING", "COMPLETED"]
    with session_scope(factory) as s:
        assert s.execute(select(func.count()).select_from(Replication)).scalar_one() == 6
        assert s.execute(select(func.count()).select_from(PolicyRun)).scalar_one() == 12  # 6 reps x 2 policies
    assert Supervisor(factory, make_settings()).run_one() is False  # nothing left


def test_child_crash_marks_failed_without_partial_completion(client: TestClient, factory: sessionmaker[Session]) -> None:
    eid = queued(client)
    Supervisor(factory, make_settings(), child_target=__import__("tests.child_targets", fromlist=["x"]).crash).run_one()
    row = exp_row(factory, eid)
    assert row.status == "FAILED" and row.error_code == "CHILD_CRASHED" and "exit code 7" in (row.error_summary or "")
    assert client.get(f"/api/v1/experiments/{eid}/results").status_code == 409
    assert events(client, eid)[-1] == "FAILED"


def test_crash_after_partial_progress_keeps_no_aggregates(client: TestClient, factory: sessionmaker[Session]) -> None:
    from tests import child_targets

    eid = queued(client)
    Supervisor(factory, make_settings(), child_target=child_targets.crash_after_first).run_one()
    row = exp_row(factory, eid)
    assert row.status == "FAILED" and row.error_code == "CHILD_CRASHED"
    with session_scope(factory) as s:
        from simuloom.db.models import MetricResult

        assert s.execute(select(func.count()).select_from(Replication)).scalar_one() == 1  # partial rows kept for audit
        n_agg = s.execute(select(func.count()).select_from(MetricResult).where(MetricResult.level == "AGGREGATE"))
        assert n_agg.scalar_one() == 0  # but no aggregate was written for the incomplete experiment
    assert client.get(f"/api/v1/experiments/{eid}/results").status_code == 409


def test_child_reported_error_is_recorded(client: TestClient, factory: sessionmaker[Session]) -> None:
    from tests import child_targets

    eid = queued(client)
    Supervisor(factory, make_settings(), child_target=child_targets.raise_error).run_one()
    row = exp_row(factory, eid)
    assert row.status == "FAILED" and row.error_code == "REPLICATION_ERROR" and "boom" in (row.error_summary or "")


def test_timeout_kills_child(client: TestClient, factory: sessionmaker[Session]) -> None:
    from tests import child_targets

    eid = queued(client)
    t0 = time.monotonic()
    Supervisor(factory, make_settings(experiment_timeout_seconds=1.5), child_target=child_targets.hang).run_one()
    assert time.monotonic() - t0 < 20
    row = exp_row(factory, eid)
    assert row.status == "FAILED" and row.error_code == "TIMEOUT"


def test_retry_failed_experiment_creates_new_linked_run(client: TestClient, factory: sessionmaker[Session]) -> None:
    from tests import child_targets

    eid = queued(client)
    Supervisor(factory, make_settings(), child_target=child_targets.crash).run_one()
    r = client.post(f"/api/v1/experiments/{eid}/retry")
    assert r.status_code == 202
    new = r.json()
    assert new["id"] != eid and new["status"] == "QUEUED" and new["retry_of"] == eid and new["base_seed"] == 11
    Supervisor(factory, make_settings()).run_one()
    assert exp_row(factory, new["id"]).status == "COMPLETED"
    assert exp_row(factory, eid).status == "FAILED"  # the original failure stays on record


def test_expired_lease_is_swept_as_interrupted(client: TestClient, factory: sessionmaker[Session]) -> None:
    eid = queued(client)
    job = claim_next(factory, "worker-a", 30.0)
    assert job is not None and exp_row(factory, eid).status == "RUNNING"
    assert sweep_expired(factory) == []  # lease still valid
    with session_scope(factory) as s:
        s.execute(update(Experiment).values(lease_expires_at=utcnow() - timedelta(seconds=1)))
    swept = sweep_expired(factory)
    assert [str(x) for x in swept] == [eid]
    row = exp_row(factory, eid)
    assert row.status == "FAILED" and row.error_code == "INTERRUPTED"
    assert heartbeat(factory, job, 30.0) is False  # a zombie worker cannot renew a lost lease
    assert events(client, eid) == ["QUEUED", "RUNNING", "FAILED"]


def test_heartbeat_extends_lease_and_records_progress(client: TestClient, factory: sessionmaker[Session]) -> None:
    eid = queued(client)
    job = claim_next(factory, "worker-a", 5.0)
    assert job is not None
    before = exp_row(factory, eid).lease_expires_at
    assert heartbeat(factory, job, 60.0, done=3) is True
    row = exp_row(factory, eid)
    assert row.replications_done == 3 and before is not None and row.lease_expires_at is not None
    assert row.lease_expires_at > before


def test_claim_is_exclusive_under_concurrency(client: TestClient, factory: sessionmaker[Session]) -> None:
    eid = queued(client)
    got: list[Any] = []
    barrier = threading.Barrier(6)

    def worker(i: int) -> None:
        barrier.wait()
        got.append(claim_next(factory, f"w{i}", 30.0))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    winners = [g for g in got if g is not None]
    assert len(winners) == 1 and str(winners[0].experiment_id) == eid
    assert exp_row(factory, eid).attempt == 1


def test_fifo_claim_order(client: TestClient, factory: sessionmaker[Session]) -> None:
    sid = make_scenario(client)["id"]
    ids = [
        client.post("/api/v1/experiments", json={"scenario_id": sid, "policies": POLICIES, "base_seed": i}).json()["id"]
        for i in (1, 2, 3)
    ]
    order = [str(claim_next(factory, "w", 30.0).experiment_id) for _ in range(3)]  # type: ignore[union-attr]
    assert order == ids


def test_running_experiment_cannot_be_deleted_and_shutdown_interrupts(client: TestClient, factory: sessionmaker[Session]) -> None:
    from tests import child_targets

    eid = queued(client)
    sup = Supervisor(factory, make_settings(), child_target=child_targets.hang)
    t = threading.Thread(target=sup.run_one)
    t.start()
    for _ in range(100):
        if exp_row(factory, eid).status == "RUNNING":
            break
        time.sleep(0.05)
    assert client.delete(f"/api/v1/experiments/{eid}").status_code == 409
    assert client.get(f"/api/v1/experiments/{eid}").json()["status"] == "RUNNING"
    sup._stop.set()  # simulate server shutdown
    t.join(30)
    row = exp_row(factory, eid)
    assert row.status == "FAILED" and row.error_code == "INTERRUPTED"


def test_background_threads_end_to_end(db_engine: Any, factory: sessionmaker[Session]) -> None:
    """Start the app with the real supervisor threads: submit over HTTP and poll to completion."""
    from simuloom.api.app import create_app

    app = create_app(make_settings(start_supervisor=True), engine=db_engine)
    with TestClient(app) as c:
        assert c.get("/health").json()["supervisor"] == "running"
        eid = submit(c, make_scenario(c)["id"])["id"]
        for _ in range(240):
            st = c.get(f"/api/v1/experiments/{eid}").json()["status"]
            if st in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.25)
        assert st == "COMPLETED"
        assert c.get(f"/api/v1/experiments/{eid}/results").status_code == 200


def test_startup_sweep_recovers_stale_running_experiment(db_engine: Any, factory: sessionmaker[Session]) -> None:
    from simuloom.api.app import create_app

    plain = create_app(make_settings(), engine=db_engine)
    with TestClient(plain) as c:
        eid = submit(c, make_scenario(c)["id"])["id"]
        claim_next(factory, "dead-server", 30.0)
        with session_scope(factory) as s:
            s.execute(update(Experiment).values(lease_expires_at=utcnow() - timedelta(minutes=5)))
    with TestClient(create_app(make_settings(start_supervisor=True), engine=db_engine)) as c2:  # "restart"
        row = c2.get(f"/api/v1/experiments/{eid}").json()
        assert row["status"] == "FAILED" and row["error_code"] == "INTERRUPTED"
