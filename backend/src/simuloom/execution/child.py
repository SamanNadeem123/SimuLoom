"""Child-process entry point. Runs in a fresh ``spawn`` interpreter; never touches the database.

Protocol (multiprocessing queue, parent reads):
  ("rep", index, ReplicationResult) per replication, then ("done",)
  ("error", failing_replication_index, exception_type, message) on any exception
"""

from __future__ import annotations

from typing import Any

from simuloom.domain.config.models import ScenarioConfig
from simuloom.services.runner import PolicySpec, run_replication


def child_main(payload: dict[str, Any], queue: Any) -> None:
    current = -1
    try:
        config = ScenarioConfig.model_validate(payload["config"])
        specs = [PolicySpec(p["label"], p["key"], p.get("params") or {}) for p in payload["policies"]]
        for i in range(int(payload["replications"])):
            current = i
            queue.put(("rep", i, run_replication(config, int(payload["base_seed"]), i, specs, keep_detail=(i == 0))))
        queue.put(("done",))
    except BaseException as exc:  # report everything, including KeyboardInterrupt/SystemExit
        queue.put(("error", current, type(exc).__name__, str(exc)[:500]))
