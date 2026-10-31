import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Card, Empty, ErrorBox, Loading } from "../components/ui";
import { useAsync } from "../hooks";
import { num, when } from "../format";

export function Scenarios() {
  const list = useAsync(() => api.listScenarios(), []);
  const demos = useAsync(() => api.demos(), []);
  const nav = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<Error | undefined>();

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setActionError(undefined);
    try {
      await fn();
      list.reload();
    } catch (e) {
      setActionError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Scenarios</h1>
          <p className="sub">A scenario describes a service centre: counters, visitor groups, workload and fairness constraints. Every edit creates a new immutable version.</p>
        </div>
        <Link className="btn btn-primary" to="/scenarios/new">
          New scenario
        </Link>
      </div>
      {actionError ? <ErrorBox error={actionError} /> : null}
      <Card title="Your scenarios">
        {list.loading ? (
          <Loading />
        ) : list.error ? (
          <ErrorBox error={list.error} onRetry={list.reload} />
        ) : list.data && list.data.items.length === 0 ? (
          <Empty title="No scenarios yet">Create one from a template below or with “New scenario”.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th className="num">Version</th>
                  <th className="num">Counters</th>
                  <th className="num">Groups</th>
                  <th>Updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.data?.items.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <Link to={`/scenarios/${s.id}`}>{s.name}</Link>
                      <div className="small">{s.description}</div>
                    </td>
                    <td className="num">{s.latest_version.version_number}</td>
                    <td className="num">{s.latest_version.config.counters}</td>
                    <td className="num">{s.latest_version.config.groups.length}</td>
                    <td>{when(s.updated_at)}</td>
                    <td>
                      <div className="row">
                        <Link className="btn btn-sm btn-primary" to={`/experiments/new?scenario=${s.id}`}>
                          Run experiment
                        </Link>
                        <button className="btn btn-sm" disabled={busy === s.id} onClick={() => void act(s.id, async () => nav(`/scenarios/${(await api.duplicateScenario(s.id)).id}`))}>
                          Duplicate
                        </button>
                        <button
                          className="btn btn-sm btn-danger"
                          disabled={busy === s.id}
                          onClick={() => {
                            if (window.confirm(`Archive “${s.name}”? Existing experiments and their snapshots are kept.`)) void act(s.id, () => api.archiveScenario(s.id));
                          }}
                        >
                          Archive
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Templates">
        <p className="small">Synthetic example scenarios supplied by the backend — not real service-centre data.</p>
        {demos.loading ? <Loading /> : demos.error ? <ErrorBox error={demos.error} onRetry={demos.reload} /> : null}
        <div className="grid cols-3">
          {demos.data?.map((d) => (
            <div key={d.key} className="constraint s-NOT_EVALUABLE">
              <strong>{d.name}</strong>
              <p>{d.description}</p>
              <p className="small">Offered load ≈ {num(d.offered_load, 2)}</p>
              <button className="btn btn-sm btn-primary" disabled={busy === d.key} onClick={() => void act(d.key, async () => nav(`/scenarios/${(await api.instantiateDemo(d.key)).id}`))}>
                {busy === d.key ? "Creating…" : "Create from template"}
              </button>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}
