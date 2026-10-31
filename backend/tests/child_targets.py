"""Module-level child entry points for failure tests (must be importable by a spawned interpreter)."""

from __future__ import annotations

import os
import time
from typing import Any

from simuloom.execution.child import child_main


def crash(payload: dict[str, Any], queue: Any) -> None:
    os._exit(7)


def hang(payload: dict[str, Any], queue: Any) -> None:
    time.sleep(600)


def raise_error(payload: dict[str, Any], queue: Any) -> None:
    queue.put(("error", 1, "RuntimeError", "boom"))


def crash_after_first(payload: dict[str, Any], queue: Any) -> None:
    from simuloom.domain.config.models import ScenarioConfig
    from simuloom.services.runner import PolicySpec, run_replication

    cfg = ScenarioConfig.model_validate(payload["config"])
    specs = [PolicySpec(p["label"], p["key"], p.get("params") or {}) for p in payload["policies"]]
    queue.put(("rep", 0, run_replication(cfg, int(payload["base_seed"]), 0, specs, keep_detail=True)))
    time.sleep(0.5)
    os._exit(9)


real = child_main
