"""Shared fixtures. Database tests need PostgreSQL: set SIMULOOM_TEST_DATABASE_URL (otherwise they are skipped)."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from simuloom.api.app import create_app
from simuloom.db.base import make_session_factory
from simuloom.settings import Settings

TEST_URL = os.environ.get("SIMULOOM_TEST_DATABASE_URL", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TABLES = (
    "constraint_evaluation, metric_result, run_timeseries, visitor_outcome, policy_run, replication, "
    "experiment_status_event, experiment, scenario_version, scenario"
)


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    if not TEST_URL:
        pytest.skip("SIMULOOM_TEST_DATABASE_URL is not set (PostgreSQL required)")
    eng = create_engine(TEST_URL, future=True)
    with eng.begin() as c:
        c.execute(text("drop schema public cascade; create schema public"))
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    cfg.cmd_opts = type("O", (), {"x": [f"url={TEST_URL}"]})()  # type: ignore[attr-defined]
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(db_engine: Engine) -> Iterator[sessionmaker[Session]]:
    with db_engine.begin() as c:
        c.execute(text(f"truncate {TABLES} restart identity cascade"))
    yield make_session_factory(db_engine)


def make_settings(**over: object) -> Settings:
    base = dict(database_url=TEST_URL, start_supervisor=False, poll_seconds=0.05, heartbeat_seconds=0.2,
                lease_seconds=5.0, sweep_seconds=0.2, experiment_timeout_seconds=60.0)  # fmt: skip
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture()
def client(db_engine: Engine, factory: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(make_settings(), engine=db_engine)
    with TestClient(app) as c:
        yield c
