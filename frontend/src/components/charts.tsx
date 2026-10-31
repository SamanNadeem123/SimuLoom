import { Bar, BarChart, CartesianGrid, ErrorBar, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { metricValue } from "../format";
import type { MetricAgg, PolicyResult, Timeseries } from "../types";

export const PALETTE = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"];

function axisFmt(unit: string) {
  return (v: number) => (unit === "proportion" ? `${Math.round(v * 100)}%` : String(Math.round(v * 100) / 100));
}

/** One bar per policy for one metric (overall group), with the confidence interval as error bars. */
export function PolicyMetricChart({ policies, metricKey, group, unit }: { policies: PolicyResult[]; metricKey: string; group: string; unit: string }) {
  const rows = policies.map((p) => {
    const m = p.metrics.find((x) => x.metric_key === metricKey && x.group_key === group);
    const evaluable = m && m.mean !== null;
    return {
      name: p.label,
      mean: evaluable ? m.mean : null,
      err: evaluable && m.ci_low !== null && m.ci_high !== null ? [m.mean! - m.ci_low, m.ci_high - m.mean!] : undefined,
    };
  });
  if (rows.every((r) => r.mean === null)) return <p className="state">This metric is not evaluable for any policy in this experiment.</p>;
  return (
    <div className="chart" role="img" aria-label={`Bar chart of ${metricKey} by policy with confidence intervals`}>
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 12, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="name" stroke="var(--muted)" />
          <YAxis stroke="var(--muted)" tickFormatter={axisFmt(unit)} />
          <Tooltip formatter={(v: number) => metricValue(v, unit)} contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)" }} />
          <Bar dataKey="mean" isAnimationActive={false} fill="var(--c1)" radius={[4, 4, 0, 0]} name="Mean over replications">
            <ErrorBar dataKey="err" width={6} stroke="var(--text)" />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Grouped bars: one cluster per visitor group, one bar per policy. */
export function GroupMetricChart({ policies, metricKey, groups, unit }: { policies: PolicyResult[]; metricKey: string; groups: string[]; unit: string }) {
  const rows = groups.map((g) => {
    const row: Record<string, string | number | null> = { group: g };
    for (const p of policies) {
      const m: MetricAgg | undefined = p.metrics.find((x) => x.metric_key === metricKey && x.group_key === g);
      row[p.label] = m && m.mean !== null ? m.mean : null;
    }
    return row;
  });
  if (rows.every((r) => policies.every((p) => r[p.label] === null))) {
    return <p className="state">No group has enough observations for this metric (minimum sample rules apply).</p>;
  }
  return (
    <div className="chart" role="img" aria-label={`Grouped bar chart of ${metricKey} by visitor group and policy`}>
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 12, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="group" stroke="var(--muted)" />
          <YAxis stroke="var(--muted)" tickFormatter={axisFmt(unit)} />
          <Tooltip formatter={(v: number) => metricValue(v, unit)} contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)" }} />
          <Legend />
          {policies.map((p, i) => (
            <Bar key={p.label} isAnimationActive={false} dataKey={p.label} fill={PALETTE[i % PALETTE.length]} radius={[3, 3, 0, 0]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function QueueChart({ ts }: { ts: Timeseries }) {
  const rows = ts.points.map((p) => ({ t: p[0] ?? 0, queue: p[1] ?? 0, busy: p[2] ?? 0 }));
  return (
    <div className="chart" role="img" aria-label="Queue length and busy counters over time">
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 12, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} stroke="var(--muted)" label={{ value: "minutes", position: "insideBottomRight", offset: -2, fill: "var(--muted)" }} />
          <YAxis stroke="var(--muted)" allowDecimals={false} />
          <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)" }} />
          <Legend />
          <Line isAnimationActive={false} type="stepAfter" dataKey="queue" name="Waiting" stroke="var(--c1)" dot={false} strokeWidth={2} />
          <Line isAnimationActive={false} type="stepAfter" dataKey="busy" name="Busy counters" stroke="var(--c2)" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
