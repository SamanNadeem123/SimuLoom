import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { GroupMetricChart, PolicyMetricChart, QueueChart } from "../components/charts";
import { Badge, Card, Empty, ErrorBox, Field, Loading, Warnings } from "../components/ui";
import { useAsync } from "../hooks";
import { NOT_EVALUABLE_HELP, ci, humanize, metricValue, num, when } from "../format";
import type { ConstraintRow, Experiment, MetricAgg, Results } from "../types";

const TABS = ["overview", "fairness", "comparison", "dynamics", "reproducibility"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = {
  overview: "Overview",
  fairness: "Fairness",
  comparison: "Policy comparison",
  dynamics: "Queue dynamics",
  reproducibility: "Reproducibility",
};
const HEADLINE = ["wait_mean", "wait_p95", "sla_attainment", "long_wait_rate", "abandonment_rate", "utilization", "throughput_per_hour", "queue_length_mean"];
const GROUP_METRICS = ["wait_mean", "sla_attainment", "long_wait_rate", "abandonment_rate"];

export function ExperimentDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [tab, setTab] = useState<Tab>("overview");
  const exp = useAsync(
    () => api.getExperiment(id),
    [id],
    (d) => (d && (d.status === "QUEUED" || d.status === "RUNNING") ? 1500 : null),
  );
  const [actionError, setActionError] = useState<Error | undefined>();

  if (exp.loading) return <Loading label="Loading experiment…" />;
  if (exp.error && !exp.data) return <ErrorBox error={exp.error} onRetry={exp.reload} />;
  const e = exp.data!;

  const retry = async () => {
    try {
      nav(`/experiments/${(await api.retryExperiment(e.id)).id}`);
    } catch (err) {
      setActionError(err instanceof Error ? err : new Error(String(err)));
    }
  };
  const remove = async () => {
    if (!window.confirm("Delete this experiment and all its stored results?")) return;
    try {
      await api.deleteExperiment(e.id);
      nav("/experiments");
    } catch (err) {
      setActionError(err instanceof Error ? err : new Error(String(err)));
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{e.scenario_name}</h1>
          <p className="sub">
            Experiment <code>{e.id.slice(0, 8)}</code> · {e.policies.map((p) => p.label).join(", ")} · created {when(e.created_at)}
          </p>
        </div>
        <div className="row">
          <Badge value={e.status} />
          {e.experiment_constraint_label ? <Badge value={e.experiment_constraint_label} title="Experiment-level fairness verdict" /> : null}
          <button className="btn btn-sm btn-danger" onClick={() => void remove()} disabled={e.status === "RUNNING"}>
            Delete
          </button>
        </div>
      </div>
      {actionError ? <ErrorBox error={actionError} /> : null}
      {exp.error ? <ErrorBox error={exp.error} onRetry={exp.reload} /> : null}
      {e.status === "QUEUED" || e.status === "RUNNING" ? <Progress e={e} /> : null}
      {e.status === "FAILED" ? (
        <div className="alert alert-error" role="alert">
          <strong>This experiment failed ({humanize(e.error_code)})</strong>
          <p>{e.error_summary}</p>
          <p className="small">No partial results are shown as final: aggregates exist only for completed experiments.</p>
          <button className="btn" onClick={() => void retry()}>
            Retry with the same snapshot and seed
          </button>
        </div>
      ) : null}
      {e.retry_of ? (
        <p className="small">
          Retry of <Link to={`/experiments/${e.retry_of}`}>{e.retry_of.slice(0, 8)}</Link>
        </p>
      ) : null}
      {e.status === "COMPLETED" ? (
        <>
          <div className="tabs" role="tablist">
            {TABS.map((t) => (
              <button key={t} role="tab" className="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                {TAB_LABEL[t]}
              </button>
            ))}
          </div>
          {tab === "reproducibility" ? <Reproducibility e={e} /> : <ResultsTabs e={e} tab={tab} />}
        </>
      ) : e.status === "FAILED" ? (
        <Reproducibility e={e} />
      ) : null}
    </>
  );
}

function Progress({ e }: { e: Experiment }) {
  const pct = e.replications_total ? Math.round((e.replications_done / e.replications_total) * 100) : 0;
  return (
    <Card title={e.status === "QUEUED" ? "Waiting for a worker…" : "Running simulation"}>
      <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${pct}%` }} />
      </div>
      <p className="small">
        {e.replications_done} of {e.replications_total} replications finished · this page refreshes automatically
      </p>
    </Card>
  );
}

function ResultsTabs({ e, tab }: { e: Experiment; tab: Tab }) {
  const results = useAsync(() => api.results(e.id), [e.id]);
  if (results.loading) return <Loading label="Loading results…" />;
  if (results.error) return <ErrorBox error={results.error} onRetry={results.reload} />;
  const r = results.data!;
  return (
    <>
      <Warnings items={r.warnings} />
      {tab === "overview" ? <Overview r={r} id={e.id} /> : null}
      {tab === "fairness" ? <Fairness r={r} id={e.id} /> : null}
      {tab === "comparison" ? <PolicyComparison r={r} id={e.id} /> : null}
      {tab === "dynamics" ? <Dynamics r={r} id={e.id} /> : null}
    </>
  );
}

function cell(m: MetricAgg | undefined) {
  if (!m) return <span className="small">—</span>;
  if (m.mean === null) {
    const why = m.reason ? (NOT_EVALUABLE_HELP[m.reason] ?? humanize(m.reason)) : "";
    return (
      <span title={why}>
        <Badge value="NOT_EVALUABLE" /> <span className="small">{humanize(m.reason)}</span>
      </span>
    );
  }
  return (
    <span title={`n=${m.n_valid}/${m.n_total} replications`}>
      {metricValue(m.mean, m.unit)}
      <div className="small">{ci(m.ci_low, m.ci_high, m.unit)}</div>
    </span>
  );
}

function Overview({ r }: { r: Results; id: string }) {
  const [metric, setMetric] = useState("wait_mean");
  const unit = r.metric_catalog[metric]?.unit ?? "";
  const keys = HEADLINE.filter((k) => r.metric_catalog[k]);
  return (
    <>
      <Card title="Headline metrics (mean over replications, with confidence interval)">
        <p className="small">
          Overall group, measurement window after warm-up, {r.replications} replications, {Math.round(((r.policies[0]?.metrics[0]?.ci_level ?? 0.95) * 100))}% Student-t intervals. Hover a cell for sample sizes.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Metric</th>
                {r.policies.map((p) => (
                  <th key={p.label}>{p.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k}>
                  <td title={r.metric_catalog[k]?.description}>{humanize(k)}</td>
                  {r.policies.map((p) => (
                    <td key={p.label}>{cell(p.metrics.find((m) => m.metric_key === k && m.group_key === "__all__"))}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card
        title="Chart"
        actions={
          <select aria-label="Metric to chart" value={metric} onChange={(ev) => setMetric(ev.target.value)} style={{ width: "auto" }}>
            {keys.map((k) => (
              <option key={k} value={k}>
                {humanize(k)}
              </option>
            ))}
          </select>
        }
      >
        <PolicyMetricChart policies={r.policies} metricKey={metric} group="__all__" unit={unit} />
        <p className="legend-note">Bars: mean over replications. Whiskers: confidence interval of the mean. {r.metric_catalog[metric]?.description}</p>
      </Card>
    </>
  );
}

function Fairness({ r, id }: { r: Results; id: string }) {
  const cons = useAsync(() => api.constraints(id), [id]);
  const [metric, setMetric] = useState("wait_mean");
  const groups = r.group_keys.length ? r.group_keys : [...new Set(r.policies.flatMap((p) => p.metrics.map((m) => m.group_key)))].filter((g) => g !== "__all__");
  const unit = r.metric_catalog[metric]?.unit ?? "";
  return (
    <>
      <Card
        title="Group comparison"
        actions={
          <select aria-label="Group metric" value={metric} onChange={(ev) => setMetric(ev.target.value)} style={{ width: "auto" }}>
            {GROUP_METRICS.filter((k) => r.metric_catalog[k]).map((k) => (
              <option key={k} value={k}>
                {humanize(k)}
              </option>
            ))}
          </select>
        }
      >
        <GroupMetricChart policies={r.policies} metricKey={metric} groups={groups} unit={unit} />
        <p className="legend-note">Groups below the minimum-sample rule are not plotted rather than shown with unreliable values.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Group</th>
                {r.policies.map((p) => (
                  <th key={p.label}>{p.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr key={g}>
                  <td>{g}</td>
                  {r.policies.map((p) => (
                    <td key={p.label}>{cell(p.metrics.find((m) => m.metric_key === metric && m.group_key === g))}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Constraint evaluation">
        {cons.loading ? <Loading /> : cons.error ? <ErrorBox error={cons.error} onRetry={cons.reload} /> : null}
        {cons.data ? (
          <>
            <div className="alert alert-info">
              <strong>
                Experiment verdict: <Badge value={cons.data.experiment_constraint_label} />
              </strong>
              <p>{cons.data.statement}</p>
            </div>
            {cons.data.policies.map((p) => (
              <div key={p.policy_label}>
                <h3>
                  {p.policy_label} <Badge value={p.constraint_label} />
                </h3>
                {p.constraints.length === 0 ? <Empty title="No constraints configured" /> : p.constraints.map((c, i) => <ConstraintCard key={`${c.constraint_id}-${c.group_key}-${i}`} c={c} />)}
              </div>
            ))}
          </>
        ) : null}
      </Card>
    </>
  );
}

function ConstraintCard({ c }: { c: ConstraintRow }) {
  return (
    <div className={`constraint s-${c.status}`}>
      <div className="row">
        <Badge value={c.status} />
        <strong>
          {c.constraint_id} · {humanize(c.type)}
          {c.group_key !== "__all__" ? ` · ${c.group_key}` : ""}
        </strong>
        {c.status !== "NOT_EVALUABLE" ? <Badge value={c.certainty} title="CONFIRMED: the whole confidence interval is on one side of the limit" /> : null}
      </div>
      <p>{c.explanation}</p>
      <p className="small">
        Observed {metricValue(c.observed, c.unit)} (required {c.comparator} {metricValue(c.limit, c.unit)})
        {c.ci ? ` · CI ${ci(c.ci[0], c.ci[1], c.unit)}` : ""} · evaluable replications {c.n_replications_valid}/{c.n_replications_total}
        {c.replication_pass_rate !== null ? ` · ${num(c.replication_pass_rate * 100, 0)}% of replications satisfied it` : ""}
        {c.reason_code ? ` · ${NOT_EVALUABLE_HELP[c.reason_code] ?? humanize(c.reason_code)}` : ""}
      </p>
      <p className="small">{c.enforcement.attempted_online ? "The policy tries to reduce this online (no guarantee)." : "The policy does not act on this constraint; it is only checked afterwards."}</p>
    </div>
  );
}

function PolicyComparison({ r, id }: { r: Results; id: string }) {
  const [baseline, setBaseline] = useState(r.baseline);
  const cmp = useAsync(() => api.comparison(id, baseline), [id, baseline]);
  const [metric, setMetric] = useState("wait_mean");
  const rows = useMemo(() => (cmp.data?.comparisons ?? []).filter((c) => c.metric_key === metric && c.group_key === "__all__"), [cmp.data, metric]);
  return (
    <Card title="Paired differences between policies">
      <div className="grid cols-3">
        <Field label="Baseline policy">
          <select value={baseline} onChange={(ev) => setBaseline(ev.target.value)}>
            {r.policies.map((p) => (
              <option key={p.label}>{p.label}</option>
            ))}
          </select>
        </Field>
        <Field label="Metric">
          <select value={metric} onChange={(ev) => setMetric(ev.target.value)}>
            {HEADLINE.filter((k) => r.metric_catalog[k]).map((k) => (
              <option key={k} value={k}>
                {humanize(k)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {cmp.loading ? <Loading /> : cmp.error ? <ErrorBox error={cmp.error} onRetry={cmp.reload} /> : null}
      {cmp.data ? (
        <>
          <p className="small">{cmp.data.difference_definition}</p>
          <div className={`alert ${cmp.data.equivalence.comparable ? "alert-info" : "alert-warn"}`}>
            <strong>{cmp.data.equivalence.comparable ? "Policies were run on equivalent workloads" : "Workloads are NOT verified equivalent"}</strong>
            <ul>
              {cmp.data.equivalence.checks.map((c) => (
                <li key={c.name}>
                  {c.ok ? "✓" : "✗"} {humanize(c.name)} — {c.detail}
                </li>
              ))}
            </ul>
          </div>
          {rows.length === 0 ? (
            <Empty title="No paired difference available for this metric" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Policy</th>
                    <th>vs</th>
                    <th className="num">Mean difference</th>
                    <th>CI</th>
                    <th className="num">Pairs</th>
                    <th>Interval excludes 0?</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c) => (
                    <tr key={c.policy_label}>
                      <td>{c.policy_label}</td>
                      <td>{c.baseline_label}</td>
                      <td className="num">{c.mean_difference === null ? "—" : `${c.mean_difference > 0 ? "+" : ""}${num(c.mean_difference, 3)} ${c.unit === "proportion" ? "" : c.unit}`}</td>
                      <td>{c.ci_low === null ? "not available" : `[${num(c.ci_low, 3)}, ${num(c.ci_high, 3)}]`}</td>
                      <td className="num">{c.n_pairs}</td>
                      <td>{c.ci_excludes_zero === null ? "—" : c.ci_excludes_zero ? "yes" : "no (inconclusive)"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}
    </Card>
  );
}

function Dynamics({ r, id }: { r: Results; id: string }) {
  const [policy, setPolicy] = useState(r.policies[0]?.label ?? "");
  const ts = useAsync(() => api.timeseries(id, policy, 0), [id, policy]);
  return (
    <Card
      title="Queue length over time (replication 0)"
      actions={
        <select aria-label="Policy" value={policy} onChange={(ev) => setPolicy(ev.target.value)} style={{ width: "auto" }}>
          {r.policies.map((p) => (
            <option key={p.label}>{p.label}</option>
          ))}
        </select>
      }
    >
      {ts.loading ? <Loading /> : ts.error ? <ErrorBox error={ts.error} onRetry={ts.reload} /> : null}
      {ts.data ? (
        <>
          <QueueChart ts={ts.data} />
          <p className="legend-note">{ts.data.note}</p>
        </>
      ) : null}
    </Card>
  );
}

function Reproducibility({ e }: { e: Experiment }) {
  const reps = useAsync(() => (e.status === "COMPLETED" || e.replications_done > 0 ? api.replications(e.id) : Promise.resolve(undefined)), [e.id, e.status]);
  const kinds: [string, string, string][] = [
    ["Summary (CSV)", "summary", "csv"],
    ["Per-run metrics (CSV)", "metrics", "csv"],
    ["Constraints (CSV)", "constraints", "csv"],
    ["Manifest (JSON)", "manifest", "json"],
    ["Report (Markdown)", "report", "md"],
  ];
  return (
    <>
      <Card title="Reproducibility record">
        <dl className="kv">
          <dt>Base seed</dt>
          <dd>{e.base_seed}</dd>
          <dt>Scenario</dt>
          <dd>
            {e.scenario_id ? <Link to={`/scenarios/${e.scenario_id}`}>{e.scenario_name}</Link> : e.scenario_name} {e.scenario_version ? `(version ${e.scenario_version})` : ""}
          </dd>
          <dt>Config hash</dt>
          <dd>
            <code>{e.scenario_config_hash}</code>
          </dd>
          <dt>Replications</dt>
          <dd>{e.replications_total}</dd>
          <dt>Policies</dt>
          <dd>{e.policies.map((p) => `${p.label}${Object.keys(p.params).length ? ` ${JSON.stringify(p.params)}` : ""}`).join("; ")}</dd>
          <dt>Environment</dt>
          <dd>{Object.entries(e.engine_versions).filter(([k]) => k !== "seed_scheme").map(([k, v]) => `${k} ${v}`).join(" · ")}</dd>
          <dt>Seed scheme</dt>
          <dd>{e.engine_versions.seed_scheme}</dd>
          <dt>Timing</dt>
          <dd>
            started {when(e.started_at)} · finished {when(e.finished_at)} · attempt {e.attempt}
          </dd>
        </dl>
        <p className="small">Results reproduce exactly when the same snapshot, seed, policies and replication count are run on the same library versions.</p>
      </Card>
      {e.status === "COMPLETED" ? (
        <Card title="Export">
          <div className="row">
            {kinds.map(([label, kind, fmt]) => (
              <a key={kind} className="btn" href={api.exportUrl(e.id, kind, fmt)} download>
                {label}
              </a>
            ))}
          </div>
        </Card>
      ) : null}
      <Card title="Generated workloads (one fingerprint per replication, shared by every policy)">
        {reps.loading ? <Loading /> : reps.error ? <ErrorBox error={reps.error} onRetry={reps.reload} /> : null}
        {reps.data ? (
          reps.data.items.length === 0 ? (
            <Empty title="No replications recorded" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="num">#</th>
                    <th>Status</th>
                    <th className="num">Visitors</th>
                    <th>Workload fingerprint</th>
                  </tr>
                </thead>
                <tbody>
                  {reps.data.items.map((x) => (
                    <tr key={x.replication_index}>
                      <td className="num">{x.replication_index}</td>
                      <td>
                        <Badge value={x.status} />
                      </td>
                      <td className="num">{x.visitor_count}</td>
                      <td>
                        <code>{x.workload_fingerprint.slice(0, 23)}…</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </Card>
      <Card title="Status history">
        <ul>
          {(e.status_events ?? []).map((s, i) => (
            <li key={i}>
              {when(s.at)} — {s.from ?? "∅"} → <strong>{s.to}</strong> {s.detail ? <span className="small">({s.detail})</span> : null}
            </li>
          ))}
        </ul>
      </Card>
      <details className="card">
        <summary>Scenario snapshot used for this experiment (JSON)</summary>
        <pre style={{ overflow: "auto", maxHeight: 360 }}>{JSON.stringify(e.scenario_snapshot, null, 2)}</pre>
      </details>
    </>
  );
}
