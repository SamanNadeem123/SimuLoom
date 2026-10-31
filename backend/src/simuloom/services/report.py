"""Human-readable Markdown report built only from stored experiment data."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from simuloom.db.models import Experiment
from simuloom.domain.config.models import ScenarioConfig
from simuloom.domain.metrics.types import ALL_GROUPS
from simuloom.services import reading


def _f(x: float | None, nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _ci(m: dict[str, Any]) -> str:
    if m["mean"] is None:
        return f"not evaluable ({m['reason']})"
    ci = f" [{_f(m['ci_low'])}, {_f(m['ci_high'])}]" if m["ci_low"] is not None else " (no CI: too few replications)"
    return f"{_f(m['mean'])}{ci}"


HEADLINE = ("wait_mean", "wait_p95", "sla_attainment", "abandonment_rate", "utilization", "throughput_per_hour")


def build_report_markdown(session: Session, exp: Experiment) -> str:
    cfg = ScenarioConfig.model_validate(exp.scenario_snapshot)
    info = reading.experiment_dict(session, exp)
    L: list[str] = [
        f"# SimuLoom experiment report - {exp.scenario_name}",
        "",
        f"- Experiment: `{exp.id}`",
        f"- Status: **{exp.status}**" + (f" ({exp.error_code}: {exp.error_summary})" if exp.error_code else ""),
        f"- Scenario config hash: `{exp.scenario_config_hash}`",
        f"- Base seed: `{exp.base_seed}`; replications: {exp.replication_count} (completed: {exp.replications_done})",
        f"- Created: {exp.created_at:%Y-%m-%d %H:%M:%S} UTC",
        "",
        "## Scenario",
        f"- Horizon {cfg.horizon_minutes:g} min, warm-up {cfg.warmup_minutes:g} min, {cfg.counters} counters, "
        f"queue capacity {cfg.queue_capacity if cfg.queue_capacity is not None else 'unlimited'}",
        f"- Service-level threshold {cfg.service_level.threshold_minutes:g} min; "
        f"offered load (upper bound) {cfg.offered_load():.2f}",
        "- Groups: "
        + "; ".join(
            f"{g.display_name} (`{g.key}`, share {g.arrival_share:g}, priority class {g.priority_class})"
            for g in cfg.enabled_groups()
        ),  # fmt: skip
        "",
    ]
    if info["warnings"]:
        L += ["## Warnings", *[f"- {w}" for w in info["warnings"]], ""]
    if exp.status != "COMPLETED":
        L += ["## Results", f"No results are available: the experiment is {exp.status}.", ""]
    else:
        res = reading.results_dict(session, exp)
        L += ["## Policies and headline metrics (mean over replications, 95% CI)", ""]
        header = "| Policy | " + " | ".join(HEADLINE) + " |"
        L += [header, "|" + "---|" * (len(HEADLINE) + 1)]
        for p in res["policies"]:
            by = {m["metric_key"]: m for m in p["metrics"] if m["group_key"] == ALL_GROUPS}
            cells = [_ci(by[k]) if k in by else "n/a" for k in HEADLINE]
            L.append(f"| {p['label']} (`{p['key']}` v{p['version']}) | " + " | ".join(cells) + " |")
        L += ["", "## Group waiting times (mean wait of served visitors, minutes)", ""]
        groups = res["group_keys"]
        L += ["| Policy | " + " | ".join(groups) + " |", "|" + "---|" * (len(groups) + 1)]
        for p in res["policies"]:
            by = {(m["metric_key"], m["group_key"]): m for m in p["metrics"]}
            L.append(
                f"| {p['label']} | "
                + " | ".join(_ci(by[("wait_mean", g)]) if ("wait_mean", g) in by else "n/a" for g in groups)
                + " |"
            )
        con = reading.constraints_dict(session, exp)
        L += ["", "## Fairness constraints", "", f"Experiment-level label: **{con['experiment_constraint_label']}**.",
              con["statement"], ""]  # fmt: skip
        for p in con["policies"]:
            L += [f"### {p['policy_label']} - {p['constraint_label']}", ""]
            for c in p["constraints"]:
                L.append(f"- **{c['constraint_id']}** `{c['type']}` group `{c['group_key']}`: **{c['status']}**"
                         + (f" ({c['certainty']})" if c["certainty"] and c["certainty"] != "NOT_APPLICABLE" else "")
                         + f" - {c['explanation']}")  # fmt: skip
            L.append("")
        comp = reading.comparison_dict(session, exp, None)
        L += [f"## Paired differences vs baseline `{comp['baseline']}`", "", comp["difference_definition"], ""]
        L += ["| Policy | Metric | Group | Mean difference | 95% CI | CI excludes 0 |", "|---|---|---|---|---|---|"]
        for c in comp["comparisons"]:
            ci = f"[{_f(c['ci_low'])}, {_f(c['ci_high'])}]" if c["ci_low"] is not None else "n/a"
            L.append(f"| {c['policy_label']} | {c['metric_key']} | {c['group_key']} | {_f(c['mean_difference'])} | {ci} | "
                     f"{c['ci_excludes_zero'] if c['ci_excludes_zero'] is not None else 'n/a'} |")  # fmt: skip
        L.append("")
    man = reading.manifest_dict(session, exp)
    L += [
        "## Reproducibility",
        "",
        f"- Engine versions: `{exp.engine_versions}`",
        "- Per-replication workload fingerprints:",
    ]
    L += [f"  - replication {r['index']}: `{r['workload_fingerprint']}` ({r['visitor_count']} visitors, {r['status']})"
          for r in man["replications"]]  # fmt: skip
    L += [
        "",
        man["how_to_reproduce"],
        "",
        "## Assumptions and limitations",
        "",
        *[f"- {s}" for s in reading.LIMITATIONS],
        "",
    ]
    return "\n".join(L)
