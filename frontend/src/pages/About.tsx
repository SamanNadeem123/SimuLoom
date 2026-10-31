import { api } from "../api";
import { Card, ErrorBox, Loading } from "../components/ui";
import { useAsync } from "../hooks";

export function About() {
  const meta = useAsync(() => api.metadata(), []);
  const policies = useAsync(() => api.policies(), []);
  if (meta.loading) return <Loading />;
  if (meta.error) return <ErrorBox error={meta.error} onRetry={meta.reload} />;
  const m = meta.data!;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>About this installation</h1>
          <p className="sub">Values below are reported live by the backend.</p>
        </div>
      </div>
      <div className="grid cols-2">
        <Card title="Engine">
          <dl className="kv">
            <dt>SimuLoom</dt>
            <dd>{m.app_version}</dd>
            <dt>Metric calculation</dt>
            <dd>{m.calculation_version}</dd>
            {Object.entries(m.engine).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card title="Limits">
          <dl className="kv">
            {Object.entries(m.limits).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{k.replace(/_/g, " ")}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
      <Card title="Scheduling policies">
        {policies.data?.map((p) => (
          <p key={p.key}>
            <strong>{p.display_name}</strong> (v{p.version}) — {p.description} <span className="small">{p.enforcement_notes}</span>
          </p>
        ))}
      </Card>
      <Card title="Limitations of this tool">
        <ul>
          {m.limitations.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
      </Card>
    </>
  );
}
