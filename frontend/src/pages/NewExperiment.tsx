import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { NumInput } from "../components/NumInput";
import { Card, Empty, ErrorBox, Field, Loading } from "../components/ui";
import { useAsync } from "../hooks";
import type { PolicyMeta } from "../types";

interface Sel {
  on: boolean;
  params: Record<string, number | undefined>;
}

export function NewExperiment() {
  const [qs] = useSearchParams();
  const nav = useNavigate();
  const scenarios = useAsync(() => api.listScenarios(100), []);
  const policies = useAsync(() => api.policies(), []);
  const [scenarioId, setScenarioId] = useState(qs.get("scenario") ?? "");
  const [sel, setSel] = useState<Record<string, Sel>>({});
  const [reps, setReps] = useState<number | null>(null);
  const [seed, setSeed] = useState<string>("");
  const [submitError, setSubmitError] = useState<Error | undefined>();
  const [busy, setBusy] = useState(false);
  const keyRef = useRef(crypto.randomUUID()); // one idempotency key per form instance: double-clicks cannot create two runs

  useEffect(() => {
    if (!scenarioId && scenarios.data?.items[0]) setScenarioId(scenarios.data.items[0].id);
  }, [scenarios.data, scenarioId]);
  useEffect(() => {
    if (policies.data && Object.keys(sel).length === 0) {
      setSel(Object.fromEntries(policies.data.map((p) => [p.key, { on: p.key === "fcfs" || p.key === "simuloom_fair", params: {} }])));
    }
  }, [policies.data, sel]);

  const scenario = scenarios.data?.items.find((s) => s.id === scenarioId);
  const chosen = useMemo(() => (policies.data ?? []).filter((p) => sel[p.key]?.on), [policies.data, sel]);
  const missing = chosen.flatMap((p) => Object.entries(p.parameter_schema).filter(([k, s]) => s.required && sel[p.key]?.params[k] === undefined).map(([k]) => `${p.display_name}: ${k}`));
  const effectiveReps = reps ?? scenario?.latest_version.config.replications ?? 0;

  const submit = async () => {
    setBusy(true);
    setSubmitError(undefined);
    try {
      const body: Parameters<typeof api.submitExperiment>[0] = {
        scenario_id: scenarioId,
        policies: chosen.map((p: PolicyMeta) => ({
          key: p.key,
          params: Object.fromEntries(Object.entries(sel[p.key]?.params ?? {}).filter(([, v]) => v !== undefined)),
        })),
      };
      if (reps !== null) body.replications = reps;
      if (seed.trim() !== "") body.base_seed = Number(seed);
      const ex = await api.submitExperiment(body, keyRef.current);
      nav(`/experiments/${ex.id}`);
    } catch (e) {
      setSubmitError(e instanceof Error ? e : new Error(String(e)));
      keyRef.current = crypto.randomUUID(); // a rejected request may be edited and resubmitted
    } finally {
      setBusy(false);
    }
  };

  if (scenarios.loading || policies.loading) return <Loading />;
  if (scenarios.error) return <ErrorBox error={scenarios.error} onRetry={scenarios.reload} />;
  if (policies.error) return <ErrorBox error={policies.error} onRetry={policies.reload} />;
  if (!scenarios.data?.items.length)
    return (
      <Empty title="Create a scenario first">
        <Link to="/scenarios/new">New scenario</Link>
      </Empty>
    );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>New experiment</h1>
          <p className="sub">Every selected policy runs on exactly the same generated workload in each replication, so differences come from the policy alone.</p>
        </div>
      </div>
      {submitError ? <ErrorBox error={submitError} /> : null}
      <Card title="Scenario and replications">
        <div className="grid cols-3">
          <Field label="Scenario">
            <select value={scenarioId} onChange={(e) => setScenarioId(e.target.value)}>
              {scenarios.data.items.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} (v{s.latest_version.version_number})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Replications" hint={`Scenario default: ${scenario?.latest_version.config.replications ?? "—"}`}>
            <NumInput value={effectiveReps} step={1} min={1} max={30} onChange={(v) => setReps(Math.round(v))} />
          </Field>
          <Field label="Base seed" hint="Leave empty for a random seed (it is recorded)">
            <input inputMode="numeric" value={seed} placeholder="random" onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ""))} />
          </Field>
        </div>
        {scenario ? (
          <p className="small">
            Snapshot: version {scenario.latest_version.version_number}, hash <code>{scenario.latest_version.config_hash.slice(0, 19)}…</code> — later edits to the scenario will not affect this run.
          </p>
        ) : null}
      </Card>
      <Card title="Scheduling policies">
        {policies.data?.map((p) => {
          const s = sel[p.key] ?? { on: false, params: {} };
          return (
            <div className="policy-check" key={p.key}>
              <input id={`pol-${p.key}`} type="checkbox" checked={s.on} onChange={(e) => setSel({ ...sel, [p.key]: { ...s, on: e.target.checked } })} />
              <div style={{ flex: 1 }}>
                <label htmlFor={`pol-${p.key}`}>
                  <strong>{p.display_name}</strong> <span className="small">v{p.version}</span>
                </label>
                <div className="small">{p.description}</div>
                <div className="small">{p.enforcement_notes}</div>
                {s.on && Object.keys(p.parameter_schema).length > 0 ? (
                  <div className="grid cols-3" style={{ marginTop: 8 }}>
                    {Object.entries(p.parameter_schema).map(([k, ps]) => (
                      <Field key={k} label={`${k}${ps.required ? " *" : ""}`} hint={ps.description}>
                        <NumInput
                          value={s.params[k] ?? null}
                          min={ps.minimum}
                          max={ps.maximum}
                          onChange={(v) => setSel({ ...sel, [p.key]: { ...s, params: { ...s.params, [k]: v } } })}
                        />
                      </Field>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
        {chosen.length === 0 ? <p className="field-error">Select at least one policy.</p> : null}
        {missing.length ? <p className="field-error">Required parameters missing: {missing.join(", ")}</p> : null}
      </Card>
      <button className="btn btn-primary" disabled={busy || chosen.length === 0 || missing.length > 0 || !scenarioId} onClick={() => void submit()}>
        {busy ? "Submitting…" : `Run ${chosen.length} polic${chosen.length === 1 ? "y" : "ies"} × ${effectiveReps} replications`}
      </button>
    </>
  );
}
