export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function metricValue(v: number | null | undefined, unit: string): string {
  if (v === null || v === undefined) return "—";
  if (unit === "proportion") return `${num(v * 100, 1)}%`;
  if (unit === "minute") return `${num(v, 2)} min`;
  return num(v, 2);
}

export function ci(lo: number | null | undefined, hi: number | null | undefined, unit: string): string {
  if (lo === null || lo === undefined || hi === null || hi === undefined) return "no interval";
  // A t-interval for a proportion can leave [0, 1]; the value itself cannot, so display it clipped.
  const clip = (x: number) => (unit === "proportion" ? Math.min(1, Math.max(0, x)) : x);
  return `[${metricValue(clip(lo), unit)}, ${metricValue(clip(hi), unit)}]`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function humanize(code: string | null | undefined): string {
  if (!code) return "";
  const s = code.replace(/_/g, " ").toLowerCase().replace(/\bsla\b/g, "SLA");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export const NOT_EVALUABLE_HELP: Record<string, string> = {
  INSUFFICIENT_SAMPLE: "Too few visitors in a group for a trustworthy estimate.",
  NO_OBSERVATIONS: "Nothing was observed for this metric.",
  FEATURE_DISABLED: "The feature behind this metric (e.g. abandonment) is disabled in the scenario.",
  INSUFFICIENT_GROUPS: "Fewer than two groups had enough data to compare.",
  BASELINE_TOO_SMALL: "The baseline wait was too small for a relative comparison.",
  TOO_FEW_REPLICATIONS: "Not enough replications for a confidence interval.",
  EXCESSIVE_EXCLUSION: "Too many replications were excluded as not evaluable.",
  EXCESSIVE_CENSORING: "Too many visitors were still waiting at the horizon.",
};
