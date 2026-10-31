import type {
  Comparison,
  Constraints,
  DemoScenario,
  Experiment,
  ExperimentSummary,
  FieldError,
  Metadata,
  Page,
  PolicyMeta,
  Replications,
  Results,
  Scenario,
  ScenarioConfig,
  Timeseries,
  ValidationResult,
} from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fieldErrors: FieldError[] = [],
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...(init.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Cannot reach the SimuLoom API. Is the backend running?");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const e = (body as { error?: { code: string; message: string; field_errors?: FieldError[]; request_id?: string } } | null)?.error;
    throw new ApiError(res.status, e?.code ?? "HTTP_ERROR", e?.message ?? `Request failed (${res.status}).`, e?.field_errors ?? [], e?.request_id);
  }
  return body as T;
}

const json = (method: string, body: unknown, headers?: Record<string, string>): RequestInit => ({
  method,
  body: JSON.stringify(body),
  headers,
});

export const api = {
  metadata: () => request<Metadata>(`${BASE}/metadata`),
  policies: () => request<PolicyMeta[]>(`${BASE}/policies`),
  demos: () => request<DemoScenario[]>(`${BASE}/demo-scenarios`),
  instantiateDemo: (key: string) => request<Scenario>(`${BASE}/demo-scenarios/${key}/instantiate`, { method: "POST" }),
  listScenarios: (limit = 50, offset = 0) => request<Page<Scenario>>(`${BASE}/scenarios?limit=${limit}&offset=${offset}`),
  getScenario: (id: string) => request<Scenario>(`${BASE}/scenarios/${id}`),
  createScenario: (name: string, description: string, config: ScenarioConfig) =>
    request<Scenario>(`${BASE}/scenarios`, json("POST", { name, description, config })),
  updateScenario: (id: string, body: { name?: string; description?: string; config?: ScenarioConfig }) =>
    request<Scenario>(`${BASE}/scenarios/${id}`, json("PATCH", body)),
  duplicateScenario: (id: string) => request<Scenario>(`${BASE}/scenarios/${id}/duplicate`, { method: "POST" }),
  archiveScenario: (id: string) => request<void>(`${BASE}/scenarios/${id}`, { method: "DELETE" }),
  validate: (config: ScenarioConfig) => request<ValidationResult>(`${BASE}/scenario-validations`, json("POST", { config })),
  submitExperiment: (
    body: { scenario_id: string; policies: { key: string; label?: string; params: Record<string, unknown> }[]; replications?: number; base_seed?: number },
    idempotencyKey: string,
  ) => request<Experiment>(`${BASE}/experiments`, json("POST", body, { "Idempotency-Key": idempotencyKey })),
  listExperiments: (status?: string, limit = 50, offset = 0) =>
    request<Page<ExperimentSummary>>(`${BASE}/experiments?limit=${limit}&offset=${offset}${status ? `&status=${status}` : ""}`),
  getExperiment: (id: string) => request<Experiment>(`${BASE}/experiments/${id}?include_events=true`),
  retryExperiment: (id: string) => request<Experiment>(`${BASE}/experiments/${id}/retry`, { method: "POST" }),
  deleteExperiment: (id: string) => request<void>(`${BASE}/experiments/${id}`, { method: "DELETE" }),
  results: (id: string) => request<Results>(`${BASE}/experiments/${id}/results`),
  comparison: (id: string, baseline?: string) =>
    request<Comparison>(`${BASE}/experiments/${id}/comparison${baseline ? `?baseline=${encodeURIComponent(baseline)}` : ""}`),
  constraints: (id: string) => request<Constraints>(`${BASE}/experiments/${id}/constraints`),
  replications: (id: string) => request<Replications>(`${BASE}/experiments/${id}/replications?limit=100`),
  timeseries: (id: string, policy: string, replication = 0) =>
    request<Timeseries>(`${BASE}/experiments/${id}/timeseries?policy=${encodeURIComponent(policy)}&replication=${replication}`),
  exportUrl: (id: string, kind: string, format: string) => `${BASE}/experiments/${id}/export?kind=${kind}&format=${format}`,
};
