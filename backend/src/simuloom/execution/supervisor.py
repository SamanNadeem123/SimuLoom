"""Postgres-backed job supervisor (spec 6.3 v2).

* The ``experiment`` table is the queue. A worker thread claims the oldest QUEUED row with
  ``FOR UPDATE SKIP LOCKED`` (a compare-and-set claim) and takes a time-limited lease.
* Each experiment runs in one ``spawn`` child process; the parent persists streamed replication results,
  renews the lease (heartbeat), enforces the wall-clock timeout and detects child crashes.
* COMPLETED is written only inside one transaction together with every aggregate row.
* A sweeper thread fails RUNNING experiments whose lease expired (server crash) as INTERRUPTED.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import queue as queue_mod
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session, sessionmaker

from simuloom.db.base import session_scope
from simuloom.db.models import FAILED, QUEUED, RUNNING, Experiment, utcnow
from simuloom.domain.config.models import ScenarioConfig
from simuloom.execution.child import child_main
from simuloom.services.persist import (
    add_status_event,
    persist_aggregate_and_complete,
    persist_failed_replication,
    persist_replication,
)
from simuloom.services.runner import PolicySpec, ReplicationResult, aggregate_experiment
from simuloom.settings import Settings

log = logging.getLogger("simuloom.supervisor")

ChildTarget = Callable[[dict[str, Any], Any], None]

TIMEOUT, CHILD_CRASHED, REPLICATION_ERROR, INTERRUPTED, AGGREGATION_ERROR = (
    "TIMEOUT", "CHILD_CRASHED", "REPLICATION_ERROR", "INTERRUPTED", "AGGREGATION_ERROR",
)  # fmt: skip


@dataclass(frozen=True)
class Job:
    experiment_id: uuid.UUID
    attempt: int
    owner: str
    config: dict[str, Any]
    policies: list[dict[str, Any]]
    base_seed: int
    replications: int


def make_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def claim_next(factory: sessionmaker[Session], owner: str, lease_seconds: float) -> Job | None:
    with session_scope(factory) as s:
        exp = s.execute(
            select(Experiment)
            .where(Experiment.status == QUEUED)
            .order_by(Experiment.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if exp is None:
            return None
        now = utcnow()
        exp.status = RUNNING
        exp.attempt += 1
        exp.lease_owner = owner
        exp.lease_expires_at = now + timedelta(seconds=lease_seconds)
        exp.heartbeat_at = now
        exp.started_at = now
        exp.replications_done = 0
        add_status_event(s, exp.id, QUEUED, RUNNING, f"claimed by {owner}")
        return Job(exp.id, exp.attempt, owner, dict(exp.scenario_snapshot), list(exp.requested_policies),
                   exp.base_seed, exp.replication_count)  # fmt: skip


def _cas_where(job: Job) -> tuple[Any, ...]:
    return (Experiment.id == job.experiment_id, Experiment.status == RUNNING,
            Experiment.lease_owner == job.owner, Experiment.attempt == job.attempt)  # fmt: skip


def _rowcount(result: Any) -> int:
    assert isinstance(result, CursorResult)
    return int(result.rowcount)


def heartbeat(factory: sessionmaker[Session], job: Job, lease_seconds: float, done: int | None = None) -> bool:
    """Renew the lease (and optionally record progress). False means the lease was lost."""
    now = utcnow()
    values: dict[str, Any] = {"heartbeat_at": now, "lease_expires_at": now + timedelta(seconds=lease_seconds)}
    if done is not None:
        values["replications_done"] = done
    with session_scope(factory) as s:
        return _rowcount(s.execute(update(Experiment).where(*_cas_where(job)).values(**values))) == 1


def fail_job(factory: sessionmaker[Session], job: Job, code: str, summary: str) -> bool:
    values: dict[str, Any] = {
        "status": FAILED,
        "error_code": code,
        "error_summary": summary[:2000],
        "finished_at": utcnow(),
        "lease_owner": None,
        "lease_expires_at": None,
    }
    with session_scope(factory) as s:
        n = _rowcount(s.execute(update(Experiment).where(*_cas_where(job)).values(**values)))
        if n == 1:
            add_status_event(s, job.experiment_id, RUNNING, FAILED, f"{code}: {summary[:500]}")
        return n == 1


def sweep_expired(factory: sessionmaker[Session]) -> list[uuid.UUID]:
    """Fail RUNNING experiments whose lease has expired. Safe to run concurrently (SKIP LOCKED)."""
    swept: list[uuid.UUID] = []
    with session_scope(factory) as s:
        rows = (
            s.execute(
                select(Experiment)
                .where(Experiment.status == RUNNING, Experiment.lease_expires_at < utcnow())
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )
        for exp in rows:
            exp.status = FAILED
            exp.error_code = INTERRUPTED
            exp.error_summary = "The worker stopped renewing its lease (server restart or crash); the run was interrupted."
            exp.finished_at = utcnow()
            exp.lease_owner = None
            exp.lease_expires_at = None
            add_status_event(s, exp.id, RUNNING, FAILED, INTERRUPTED)
            swept.append(exp.id)
    return swept


class Supervisor:
    def __init__(self, factory: sessionmaker[Session], settings: Settings, child_target: ChildTarget = child_main) -> None:
        self.factory = factory
        self.settings = settings
        self.child_target = child_target
        self.owner = make_owner()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._ctx = mp.get_context("spawn")

    # ---- lifecycle -------------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._worker_loop, name="simuloom-worker", daemon=True),
            threading.Thread(target=self._sweeper_loop, name="simuloom-sweeper", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        self._threads = []

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_one():
                    self._stop.wait(self.settings.poll_seconds)
            except Exception:
                log.exception("worker loop error")
                self._stop.wait(self.settings.poll_seconds)

    def _sweeper_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for exp_id in sweep_expired(self.factory):
                    log.warning("experiment %s interrupted (lease expired)", exp_id)
            except Exception:
                log.exception("sweeper error")
            self._stop.wait(self.settings.sweep_seconds)

    # ---- one job -----------------------------------------------------------------------
    def run_one(self) -> bool:
        """Claim and run one experiment synchronously. Returns False if nothing was queued."""
        job = claim_next(self.factory, self.owner, self.settings.lease_seconds)
        if job is None:
            return False
        try:
            self._run_job(job)
        except Exception as exc:  # unexpected supervisor-side failure: never leave it RUNNING silently
            log.exception("job %s crashed in supervisor", job.experiment_id)
            fail_job(self.factory, job, "SUPERVISOR_ERROR", f"{type(exc).__name__}: {exc}")
        return True

    def _run_job(self, job: Job) -> None:
        cfg = ScenarioConfig.model_validate(job.config)
        specs = [PolicySpec(p["label"], p["key"], p.get("params") or {}) for p in job.policies]
        q = self._ctx.Queue()
        proc = self._ctx.Process(
            target=self.child_target,
            args=({"config": job.config, "policies": job.policies, "base_seed": job.base_seed,
                   "replications": job.replications}, q),
            daemon=True,
        )  # fmt: skip
        proc.start()
        started = time.monotonic()
        deadline = started + self.settings.experiment_timeout_seconds
        last_beat = started
        collected: list[ReplicationResult] = []
        finished = False

        def kill() -> None:
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
                if proc.is_alive():
                    proc.kill()
                    proc.join(5)

        try:
            while not finished:
                msg: tuple[Any, ...] | None
                try:
                    msg = q.get(timeout=min(self.settings.poll_seconds, 0.25))
                except queue_mod.Empty:
                    msg = None
                now = time.monotonic()
                if self._stop.is_set():
                    kill()
                    fail_job(self.factory, job, INTERRUPTED, "The server shut down while the experiment was running.")
                    return
                if now - last_beat >= self.settings.heartbeat_seconds:
                    last_beat = now
                    if not heartbeat(self.factory, job, self.settings.lease_seconds):
                        log.warning("lease lost for %s; abandoning", job.experiment_id)
                        kill()
                        return
                if now > deadline:
                    kill()
                    limit = self.settings.experiment_timeout_seconds
                    fail_job(self.factory, job, TIMEOUT, f"The experiment exceeded the {limit:g} s time limit.")
                    return
                if msg is None:
                    if not proc.is_alive():
                        try:  # the child may have written its last message just before exiting
                            msg = q.get(timeout=1.0)
                        except queue_mod.Empty:
                            kill()
                            code = proc.exitcode
                            fail_job(
                                self.factory,
                                job,
                                CHILD_CRASHED,
                                f"The simulation process exited unexpectedly (exit code {code}).",
                            )
                            return
                    else:
                        continue
                kind = msg[0]
                if kind == "rep":
                    _, index, result = msg
                    collected.append(result)
                    try:
                        with session_scope(self.factory) as s:
                            persist_replication(s, job.experiment_id, cfg, specs, result, job.base_seed)
                    except Exception as exc:
                        kill()
                        fail_job(self.factory, job, "PERSISTENCE_ERROR", f"{type(exc).__name__}: {exc}")
                        return
                    last_beat = now
                    if not heartbeat(self.factory, job, self.settings.lease_seconds, done=len(collected)):
                        kill()
                        return
                elif kind == "done":
                    finished = True
                elif kind == "error":
                    _, index, etype, text = msg
                    kill()
                    with session_scope(self.factory) as s:
                        if index >= 0:
                            persist_failed_replication(s, job.experiment_id, index, job.base_seed, f"{etype}: {text}")
                    fail_job(self.factory, job, REPLICATION_ERROR, f"Replication {index} failed: {etype}: {text}")
                    return
            proc.join(10)
            self._complete(job, cfg, specs, collected)
        finally:
            kill()
            q.close()

    def _complete(self, job: Job, cfg: ScenarioConfig, specs: list[PolicySpec], collected: list[ReplicationResult]) -> None:
        if len(collected) != job.replications:
            fail_job(
                self.factory,
                job,
                CHILD_CRASHED,
                f"Expected {job.replications} replications, received {len(collected)}.",
            )
            return
        try:
            agg = aggregate_experiment(cfg, specs, collected)
        except Exception as exc:
            fail_job(self.factory, job, AGGREGATION_ERROR, f"{type(exc).__name__}: {exc}")
            return
        with session_scope(self.factory) as s:
            exp = s.execute(select(Experiment).where(*_cas_where(job)).with_for_update()).scalar_one_or_none()
            if exp is None:  # lease lost between the last heartbeat and now: write nothing
                s.rollback()
                return
            persist_aggregate_and_complete(s, exp, cfg, agg)
            exp.replications_done = job.replications
