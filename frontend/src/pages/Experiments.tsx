import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Badge, Card, Empty, ErrorBox, Loading } from "../components/ui";
import { useAsync } from "../hooks";
import { when } from "../format";

export function Experiments() {
  const [status, setStatus] = useState("");
  const list = useAsync(
    () => api.listExperiments(status || undefined),
    [status],
    (d) => (d?.items.some((e) => e.status === "QUEUED" || e.status === "RUNNING") ? 2000 : null),
  );
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Experiments</h1>
          <p className="sub">Each experiment stores its own scenario snapshot, seed and results.</p>
        </div>
        <div className="row">
          <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: "auto" }}>
            <option value="">All statuses</option>
            {["QUEUED", "RUNNING", "COMPLETED", "FAILED"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <Link className="btn btn-primary" to="/experiments/new">
            New experiment
          </Link>
        </div>
      </div>
      <Card>
        {list.loading ? (
          <Loading />
        ) : list.error ? (
          <ErrorBox error={list.error} onRetry={list.reload} />
        ) : list.data?.items.length === 0 ? (
          <Empty title={status ? `No ${status} experiments` : "No experiments yet"}>
            <Link to="/experiments/new">Run your first experiment</Link>
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Experiment</th>
                  <th>Status</th>
                  <th>Policies</th>
                  <th>Progress</th>
                  <th>Fairness verdict</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {list.data?.items.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <Link to={`/experiments/${e.id}`}>{e.scenario_name}</Link>
                      <div className="small">
                        <code>{e.id.slice(0, 8)}</code> · seed {e.base_seed}
                      </div>
                    </td>
                    <td>
                      <Badge value={e.status} title={e.error_code ?? undefined} />
                    </td>
                    <td className="small">{e.policies.join(", ")}</td>
                    <td className="small">
                      {e.replications_done}/{e.replications_total}
                    </td>
                    <td>{e.experiment_constraint_label ? <Badge value={e.experiment_constraint_label} /> : "—"}</td>
                    <td className="small">{when(e.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
