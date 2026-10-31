"""Spike tests SP-01..SP-10 (Phase 0 spec 4.6)."""

import hashlib
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import simpy

from spikes.dispatcher_proto import INF, V, fcfs, idmod, lifo, run, static

ROOT = Path(__file__).resolve().parent.parent


def tie_workload(seed: int, n: int = 60) -> list[V]:
    rng = np.random.default_rng(seed)
    arr = np.sort(rng.integers(0, 40, n) / 2.0)
    out = []
    for i in range(n):
        pat = INF if rng.random() < 0.5 else float(rng.integers(1, 12)) / 2.0
        out.append(
            V(
                i,
                float(arr[i]),
                float(rng.integers(1, 10)) / 2.0,
                pat,
                bool(rng.random() < 0.05),
                int(rng.integers(1, 4)),
            )
        )
    return out


def test_sp01_capacity():
    for seed in range(200):
        c = 1 + seed % 3
        R = run(
            tie_workload(seed),
            c,
            25.0,
            [fcfs, lifo, static, idmod][seed % 4],
            capacity=None if seed % 2 else 3,
        )
        assert R.max_busy <= c and R.max_res_count <= c
        assert R.max_busy >= 1


def test_sp02_creation_order_independence():
    w = tie_workload(7)
    base = run(w, 2, 25.0)
    ids = [v.id for v in w]
    rnd = random.Random(1)
    for _ in range(50):
        rnd.shuffle(ids)
        R = run(w, 2, 25.0, creation_order=ids)
        assert R.trace == base.trace and R.outcome == base.outcome
    assert len({t for t, _, _ in base.trace}) < len(base.trace)  # ties really present


def test_sp03_arrival_and_completion_same_time():
    R = run([V(0, 0.0, 4.0), V(1, 4.0, 1.0)], 1, 20.0)
    assert R.outcome[1]["B"] == 4.0 and R.outcome[0]["C"] == 4.0
    kinds = [k for t, k, _ in R.trace if t == 4.0]
    assert kinds == ["COMPLETE", "ARRIVE", "START"]


@pytest.mark.parametrize("sel", [fcfs, lifo, static, idmod])
def test_sp04_abandon_beats_service(sel):
    vs = [V(0, 0.0, 4.0), V(1, 1.0, 2.0, patience=3.0, prio=0), V(2, 2.0, 2.0, prio=5)]
    R = run(vs, 1, 30.0, sel)
    assert R.outcome[1]["status"] == "ABANDONED" and R.outcome[1]["Z"] == 4.0
    assert R.outcome[2]["B"] == 4.0
    assert {i for i, o in R.outcome.items() if o["status"] == "ABANDONED"} == {1}


def test_sp05_settle_discipline():
    for seed in range(50):
        R = run(tie_workload(seed), 2, 25.0)
        times = [t for t, _ in R.settle_log]
        assert times == sorted(set(times))  # at most once per timestamp, strictly increasing
        assert all(p > t for t, p in R.settle_log[:-1])  # settle only when peek() > now
        assert R.steps_with_state_change_outside_settle == 0


def test_sp06_capacity_blocking():
    r = run([V(0, 0.0, 10.0), V(1, 1.0, 1.0), V(2, 1.0, 1.0), V(3, 2.0, 1.0)], 1, 20.0, capacity=1)
    blocked = {i for i, o in r.outcome.items() if o["status"] == "BLOCKED"}
    assert blocked == {2, 3} and r.outcome[1]["B"] == 10.0
    r = run([V(0, 0.0, 10.0), V(1, 1.0, 1.0)], 1, 20.0, capacity=0)
    assert r.outcome[0]["B"] == 0.0 and r.outcome[1]["status"] == "BLOCKED"
    r = run([V(i, 0.0, 5.0) for i in range(4)], 2, 20.0, capacity=1)
    assert {i for i, o in r.outcome.items() if o["status"] == "BLOCKED"} == {3}
    assert r.outcome[0]["B"] == r.outcome[1]["B"] == 0.0 and r.outcome[2]["B"] == 5.0


def test_sp07_horizon():
    r = run([V(0, 0.0, 10.0), V(1, 9.0, 1.0, patience=1.0)], 1, 10.0)
    assert r.outcome[0]["status"] == "COMPLETED" and r.outcome[0]["C"] == 10.0
    assert r.outcome[1]["status"] == "ABANDONED" and r.outcome[1]["Z"] == 10.0
    r = run([V(0, 0.0, 10.5), V(1, 1.0, 1.0)], 1, 10.0)
    assert r.outcome[0]["status"] == "IN_SERVICE_AT_HORIZON"
    assert r.outcome[1]["status"] == "WAITING_AT_HORIZON"
    with pytest.raises(AssertionError):  # arrival at H is rejected
        run([V(0, 10.0, 1.0)], 1, 10.0)


def _digest() -> str:
    R = run(tie_workload(11), 2, 25.0)
    return hashlib.sha256(repr((R.trace, sorted(R.outcome.items()))).encode()).hexdigest()


def test_sp08_determinism():
    assert _digest() == _digest()
    out = subprocess.run(
        [sys.executable, "-c", "from spikes.test_sp import _digest; print(_digest())"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(ROOT), "PATH": ""},
    ).stdout.strip()
    assert out == _digest()


def test_sp09_stale_timer():
    r = run([V(0, 0.0, 1.0), V(1, 0.0, 1.0, patience=5.0)], 1, 10.0)
    assert r.outcome[1]["status"] == "COMPLETED"
    assert 5.0 not in [t for t, _ in r.settle_log]
    assert all(k != "ABANDON" for _, k, _ in r.trace)


def test_sp10_semantics():
    env = simpy.Environment()
    assert env.peek() == INF  # V-1
    with pytest.raises(simpy.core.EmptySchedule):
        env.step()
    log: list[str] = []  # V-2/V-4/V-6
    env = simpy.Environment()
    env.timeout(0).callbacks.append(lambda e: log.append("timeout0"))

    def gen():
        log.append("proc_start")
        yield env.timeout(1)
        log.append("resumed")
        env.timeout(0).callbacks.append(lambda e: log.append("follow"))
        yield env.timeout(2)

    env.process(gen())
    env.step()
    assert log == ["proc_start"]  # URGENT Initialize beats earlier NORMAL timeout at same time
    env.step()
    assert log == ["proc_start", "timeout0"] and env.now == 0
    env.step()  # timeout(1): process resumes inside this very step
    assert env.now == 1 and log[-1] == "resumed" and env.peek() == 1  # follow-on at same time pending
    env.step()
    assert log[-1] == "follow"
    env = simpy.Environment()  # V-7
    seen: list[float] = []
    env.timeout(5).callbacks.append(lambda e: seen.append(env.now))
    env.run(until=5)
    assert seen == [] and env.now == 5
    env = simpy.Environment()  # V-5
    res = simpy.Resource(env, 1)
    rq = res.request()
    assert rq.triggered and res.count == 1
    rq2 = res.request()
    assert not rq2.triggered
    res.release(rq)
    assert not rq2.triggered  # grant is a later event, not synchronous
    while env.peek() != INF:
        env.step()
    assert rq2.triggered and env.now == 0
    for x in (0.1, 0.3, 0.7, 1 / 3, 12.345):  # V-8 absolute scheduling is exact
        env = simpy.Environment()
        env.timeout(x)
        env.step()
        assert env.now == x
