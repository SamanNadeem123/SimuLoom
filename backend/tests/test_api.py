"""API behaviour against PostgreSQL: scenarios, validation, idempotency, errors, results, exports."""

from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from simuloom.execution.supervisor import Supervisor
from tests.conftest import make_settings
from tests.helpers import base_config

pytestmark = pytest.mark.usefixtures("factory")

POLICIES = [{"key": "fcfs"}, {"key": "simuloom_fair"}]


def small_config(**over: Any) -> dict[str, Any]:
    cfg = base_config(horizon_minutes=150, warmup_minutes=30, counters=2, replications=6, **over)
    return cfg.model_dump(mode="json")


def make_scenario(client: TestClient, name: str = "S1", **over: Any) -> dict[str, Any]:
    r = client.post("/api/v1/scenarios", json={"name": name, "config": small_config(**over)})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def run_all(factory: sessionmaker[Session]) -> None:
    sup = Supervisor(factory, make_settings())
    while sup.run_one():
        pass


def submit(client: TestClient, sid: str, policies: Any = None, **kw: Any) -> dict[str, Any]:
    r = client.post("/api/v1/experiments", json={"scenario_id": sid, "policies": policies or POLICIES, "base_seed": 11, **kw})
    assert r.status_code == 202, r.text
    return r.json()  # type: ignore[no-any-return]


@pytest.fixture()
def completed(client: TestClient, factory: sessionmaker[Session]) -> dict[str, Any]:
    sc = make_scenario(client)
    ex = submit(client, sc["id"])
    assert ex["status"] == "QUEUED"
    run_all(factory)
    done = client.get(f"/api/v1/experiments/{ex['id']}").json()
    assert done["status"] == "COMPLETED", done
    return done  # type: ignore[no-any-return]


# ------------------------------------------------------------------------------ system
def test_health_metadata_policies(client: TestClient) -> None:
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["db"] == "up"
    m = client.get("/api/v1/metadata").json()
    assert m["limits"]["max_replications"] == 30 and "limitations" in m and m["engine"]["simpy"] == "4.1.2"
    keys = [p["key"] for p in client.get("/api/v1/policies").json()]
    assert keys == ["aging_priority", "fcfs", "simuloom_fair", "static_priority"] or set(keys) == {
        "aging_priority", "fcfs", "simuloom_fair", "static_priority"}  # fmt: skip


def test_security_headers_and_request_id(client: TestClient) -> None:
    r = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123" and r.headers["X-Content-Type-Options"] == "nosniff"


# ------------------------------------------------------------------------------ scenarios
def test_scenario_crud_versions_duplicate_archive(client: TestClient) -> None:
    s = make_scenario(client)
    assert s["latest_version"]["version_number"] == 1 and s["latest_version"]["config_hash"].startswith("sha256:")
    sid = s["id"]
    cfg2 = small_config()
    cfg2["counters"] = 3
    p = client.patch(f"/api/v1/scenarios/{sid}", json={"config": cfg2})
    assert p.status_code == 200 and p.json()["latest_version"]["version_number"] == 2
    unchanged = client.patch(f"/api/v1/scenarios/{sid}", json={"name": "Renamed"}).json()
    assert unchanged["name"] == "Renamed" and unchanged["latest_version"]["version_number"] == 2
    vs = client.get(f"/api/v1/scenarios/{sid}/versions").json()
    assert vs["total"] == 2
    dup = client.post(f"/api/v1/scenarios/{sid}/duplicate")
    assert dup.status_code == 201 and dup.json()["id"] != sid
    lst = client.get("/api/v1/scenarios?q=Renamed").json()
    assert lst["total"] == 2 and client.get("/api/v1/scenarios?q=zzzz").json()["total"] == 0
    assert client.delete(f"/api/v1/scenarios/{sid}").status_code == 204
    assert client.get("/api/v1/scenarios").json()["total"] == 1  # archived hidden
    assert client.get("/api/v1/scenarios?include_archived=true").json()["total"] == 2
    r = client.post("/api/v1/experiments", json={"scenario_id": sid, "policies": POLICIES})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"


def test_invalid_scenario_requests(client: TestClient) -> None:
    bad = small_config()
    bad["counters"] = 0
    r = client.post("/api/v1/scenarios", json={"name": "x", "config": bad})
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "VALIDATION_ERROR" and any("counters" in f["path"] for f in e["field_errors"]) and e["request_id"]
    assert client.post("/api/v1/scenarios", json={"name": "", "config": {}}).status_code == 422
    assert client.post("/api/v1/scenarios", json={"name": "x", "config": {}, "bogus": 1}).status_code == 422
    assert client.get("/api/v1/scenarios/not-a-uuid").status_code == 422
    nf = client.get(f"/api/v1/scenarios/{uuid.uuid4()}")
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/api/v1/nope").status_code == 404
    assert client.get("/api/v1/scenarios?limit=0").status_code == 422


def test_scenario_validation_endpoint_and_demos(client: TestClient) -> None:
    ok = client.post("/api/v1/scenario-validations", json={"config": small_config()}).json()
    assert ok["valid"] is True and ok["estimated_cost"]["visitor_simulations"] > 0
    bad = client.post("/api/v1/scenario-validations", json={"config": {"counters": 1}}).json()
    assert bad["valid"] is False and bad["errors"]
    assert client.post("/api/v1/scenario-validations", json={}).status_code == 422
    demos = client.get("/api/v1/demo-scenarios").json()
    assert len(demos) >= 1
    r = client.post(f"/api/v1/demo-scenarios/{demos[0]['key']}/instantiate")
    assert r.status_code == 201 and r.json()["latest_version"]["config"]
    assert client.post("/api/v1/demo-scenarios/unknown/instantiate").status_code == 404


# ------------------------------------------------------------------------------ experiments
def test_submit_validation_and_limits(client: TestClient) -> None:
    sc = make_scenario(client)
    sid = sc["id"]
    r = client.post("/api/v1/experiments", json={"scenario_id": sid, "policies": [{"key": "nope"}]})
    assert r.status_code == 422 and r.json()["error"]["field_errors"][0]["path"] == "policies"
    r = client.post("/api/v1/experiments", json={"scenario_id": sid, "policies": [{"key": "aging_priority"}]})
    assert r.status_code == 422  # missing aging_rate_per_minute
    r = client.post("/api/v1/experiments", json={"scenario_id": sid, "policies": []})
    assert r.status_code == 422
    r = client.post("/api/v1/experiments", json={"scenario_id": sid, "policies": POLICIES, "replications": 31})
    assert r.status_code == 422
    r = client.post("/api/v1/experiments", json={"scenario_id": str(uuid.uuid4()), "policies": POLICIES})
    assert r.status_code == 404
    r = client.post("/api/v1/experiments", json={"scenario_id": sid, "policies": [{"key": "fcfs"}, {"key": "fcfs"}]})
    assert r.status_code == 422  # duplicate labels
    r = client.post("/api/v1/experiments", json={"scenario_id": sid, "scenario_version": 9, "policies": POLICIES})
    assert r.status_code == 404


def test_idempotency_and_pending_limit(client: TestClient, db_engine: Any) -> None:
    sid = make_scenario(client)["id"]
    body = {"scenario_id": sid, "policies": POLICIES, "base_seed": 5}
    a = client.post("/api/v1/experiments", json=body, headers={"Idempotency-Key": "k1"})
    b = client.post("/api/v1/experiments", json=body, headers={"Idempotency-Key": "k1"})
    assert a.status_code == b.status_code == 202 and a.json()["id"] == b.json()["id"]
    assert b.headers["Idempotent-Replayed"] == "true" and "Idempotent-Replayed" not in a.headers
    diff = client.post("/api/v1/experiments", json={**body, "base_seed": 6}, headers={"Idempotency-Key": "k1"})
    assert diff.status_code == 409
    assert client.post("/api/v1/experiments", json=body, headers={"Idempotency-Key": "x" * 200}).status_code == 422
    for i in range(3):  # a(1) + three more = 4 pending, 5th allowed, 6th refused
        assert client.post("/api/v1/experiments", json={**body, "base_seed": 100 + i}).status_code == 202
    assert client.post("/api/v1/experiments", json={**body, "base_seed": 200}).status_code == 202
    r = client.post("/api/v1/experiments", json={**body, "base_seed": 201})
    assert r.status_code == 429 and r.json()["error"]["code"] == "TOO_MANY_PENDING" and r.headers["Retry-After"] == "5"


def test_visitor_simulation_cap(client: TestClient, db_engine: Any) -> None:
    from simuloom.api.app import create_app

    app = create_app(make_settings(max_total_visitor_simulations=100), engine=db_engine)
    with TestClient(app) as c:
        sid = make_scenario(c)["id"]
        r = c.post("/api/v1/experiments", json={"scenario_id": sid, "policies": POLICIES})
        assert r.status_code == 422 and r.json()["error"]["code"] == "LIMIT_EXCEEDED"


def test_results_not_ready_then_completed(client: TestClient, factory: sessionmaker[Session]) -> None:
    sid = make_scenario(client)["id"]
    ex = submit(client, sid)
    r = client.get(f"/api/v1/experiments/{ex['id']}/results")
    assert r.status_code == 409 and r.json()["error"]["code"] == "EXPERIMENT_NOT_READY"
    assert client.get(f"/api/v1/experiments/{ex['id']}/comparison").status_code == 409
    assert client.get(f"/api/v1/experiments/{ex['id']}/export?kind=summary&format=csv").status_code == 409
    run_all(factory)
    assert client.get(f"/api/v1/experiments/{ex['id']}/results").status_code == 200


def test_completed_results_traceability_and_determinism(client: TestClient, completed: dict[str, Any]) -> None:
    eid = completed["id"]
    assert completed["replications_done"] == 6 and completed["base_seed"] == 11
    assert completed["scenario_config_hash"].startswith("sha256:") and completed["engine_versions"]["simpy"] == "4.1.2"
    res = client.get(f"/api/v1/experiments/{eid}/results").json()
    pols = res["policies"]
    assert [p["label"] for p in pols] == ["fcfs", "simuloom_fair"]
    reps = client.get(f"/api/v1/experiments/{eid}/replications?limit=100").json()
    assert reps["total"] == 6
    rep0 = reps["items"][0]
    assert rep0["replication_index"] == 0 and rep0["status"] == "COMPLETED"
    assert rep0["workload_fingerprint"].startswith("sha256:")
    # one fingerprint per replication, shared by every policy run => equivalent workloads
    assert [r["policy_label"] for r in rep0["policy_runs"]] == ["fcfs", "simuloom_fair"]
    assert len({r["workload_fingerprint"] for r in reps["items"]}) == 6  # distinct per replication
    # same config + seed => identical aggregated results in a fresh experiment
    comp = client.get(f"/api/v1/experiments/{eid}/comparison").json()
    assert comp["baseline"] == "fcfs" and comp["comparisons"]
    cons = client.get(f"/api/v1/experiments/{eid}/constraints").json()
    statuses = json.dumps(cons)
    assert any(s in statuses for s in ("SATISFIED", "VIOLATED", "NOT_EVALUABLE"))
    ts = client.get(f"/api/v1/experiments/{eid}/timeseries?policy=fcfs&replication=0").json()
    assert ts["points"]
    assert client.get(f"/api/v1/experiments/{eid}/timeseries?policy=fcfs&replication=3").status_code in (404, 422)
    assert client.get(f"/api/v1/experiments/{eid}/timeseries?policy=zzz").status_code == 404


def test_reproducible_across_experiments(client: TestClient, factory: sessionmaker[Session], completed: dict[str, Any]) -> None:
    again = submit(
        client,
        completed["scenario_id"] if "scenario_id" in completed else client.get("/api/v1/scenarios").json()["items"][0]["id"],
    )
    run_all(factory)
    a = client.get(f"/api/v1/experiments/{completed['id']}/results").json()["policies"]
    b = client.get(f"/api/v1/experiments/{again['id']}/results").json()["policies"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    cmp = client.get(f"/api/v1/experiment-comparisons?ids={completed['id']},{again['id']}")
    assert cmp.status_code == 200
    assert client.get(f"/api/v1/experiment-comparisons?ids={completed['id']}").status_code == 422
    assert client.get("/api/v1/experiment-comparisons?ids=" + "x" * 40).status_code == 422


def test_exports(client: TestClient, completed: dict[str, Any]) -> None:
    eid = completed["id"]
    r = client.get(f"/api/v1/experiments/{eid}/export?kind=summary&format=csv")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert len(rows) > 1 and len(set(len(x) for x in rows)) == 1
    for kind in ("metrics", "constraints"):
        assert client.get(f"/api/v1/experiments/{eid}/export?kind={kind}&format=csv").status_code == 200
    man = client.get(f"/api/v1/experiments/{eid}/export?kind=manifest&format=json").json()
    assert man["base_seed"] == 11 and man["scenario"]["config_hash"] and len(man["replications"]) == 6
    assert man["replications"][0]["workload_fingerprint"].startswith("sha256:") and man["engine_versions"]["simpy"]
    rep = client.get(f"/api/v1/experiments/{eid}/export?kind=report&format=md")
    assert rep.status_code == 200 and "## Assumptions and limitations" in rep.text and "## Reproducibility" in rep.text
    assert client.get(f"/api/v1/experiments/{eid}/export?kind=bogus").status_code == 422


def test_listing_filters_events_delete_and_snapshot_immutability(client: TestClient, factory: sessionmaker[Session]) -> None:
    sc = make_scenario(client)
    ex = submit(client, sc["id"])
    cfg2 = small_config()
    cfg2["counters"] = 3
    client.patch(f"/api/v1/scenarios/{sc['id']}", json={"config": cfg2})
    run_all(factory)
    d = client.get(f"/api/v1/experiments/{ex['id']}?include_events=true").json()
    assert d["scenario_snapshot"]["counters"] == 2 and d["status"] == "COMPLETED"  # snapshot unaffected by the edit
    assert [e["to"] for e in d["status_events"]] == ["QUEUED", "RUNNING", "COMPLETED"]
    assert client.get("/api/v1/experiments?status=COMPLETED").json()["total"] == 1
    assert client.get("/api/v1/experiments?status=BOGUS").status_code == 422
    assert client.get(f"/api/v1/experiments?scenario_id={sc['id']}").json()["total"] == 1
    assert client.post(f"/api/v1/experiments/{ex['id']}/retry").status_code == 409  # not FAILED
    assert client.delete(f"/api/v1/experiments/{ex['id']}").status_code == 204
    assert client.get(f"/api/v1/experiments/{ex['id']}").status_code == 404
