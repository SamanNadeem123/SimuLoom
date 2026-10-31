// Types mirror the backend API responses (see backend/src/simuloom/api and services/reading.py).

export interface FieldError {
  path: string;
  code: string;
  message: string;
}

export interface Dist {
  kind: string;
  mean_minutes?: number;
  cv?: number;
  [k: string]: unknown;
}

export interface GroupCfg {
  key: string;
  display_name: string;
  arrival_share: number;
  priority_class: number;
  enabled: boolean;
  service?: Dist | null;
}

export interface ConstraintCfg {
  id: string;
  type: string;
  unit: "minute" | "proportion";
  limit: number;
  enabled: boolean;
  group: string;
  wait_basis: string;
}

export interface ScenarioConfig {
  schema_version: number;
  time_unit: string;
  horizon_minutes: number;
  warmup_minutes: number;
  counters: number;
  queue_capacity: number | null;
  arrival: { process: string; rate_per_minute?: number; [k: string]: unknown };
  default_service: Dist;
  groups: GroupCfg[];
  abandonment: { enabled: boolean; patience: Dist | null };
  no_show: { enabled: boolean; probability: number };
  service_level: { threshold_minutes: number };
  constraints: ConstraintCfg[];
  min_sample: { group_n: number; p95_n: number };
  statistics: Record<string, number>;
  replications: number;
}

export interface ScenarioVersion {
  id: string;
  scenario_id: string;
  version_number: number;
  config_hash: string;
  created_at: string;
  config: ScenarioConfig;
}

export interface Scenario {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
  latest_version: ScenarioVersion;
  warnings: string[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface DemoScenario {
  key: string;
  name: string;
  description: string;
  synthetic: boolean;
  offered_load: number;
  config: ScenarioConfig;
}

export interface ValidationResult {
  valid: boolean;
  errors: FieldError[];
  warnings: string[];
  estimated_cost: { expected_visitors_per_run: number; replications: number; policies: number; visitor_simulations: number } | null;
  config_hash: string | null;
}

export interface ParamSchema {
  type: string;
  minimum?: number;
  maximum?: number;
  required?: boolean;
  description?: string;
}

export interface PolicyMeta {
  key: string;
  display_name: string;
  version: string;
  description: string;
  parameter_schema: Record<string, ParamSchema>;
  enforcement_notes: string;
}

export interface Metadata {
  app_version: string;
  calculation_version: string;
  engine: Record<string, string>;
  limits: Record<string, number>;
  constraint_types: string[];
  limitations: string[];
}

export type ExperimentStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";

export interface ExperimentSummary {
  id: string;
  status: ExperimentStatus;
  scenario_name: string;
  scenario_config_hash: string;
  policies: string[];
  replications_total: number;
  replications_done: number;
  base_seed: number;
  error_code: string | null;
  experiment_constraint_label: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Experiment {
  id: string;
  status: ExperimentStatus;
  scenario_id: string | null;
  scenario_version: number | null;
  scenario_name: string;
  scenario_config_hash: string;
  scenario_snapshot: ScenarioConfig;
  policies: { key: string; label: string; params: Record<string, unknown> }[];
  replications_total: number;
  replications_done: number;
  base_seed: number;
  limits: Record<string, number>;
  engine_versions: Record<string, string>;
  warnings: string[];
  error_code: string | null;
  error_summary: string | null;
  attempt: number;
  retry_of: string | null;
  experiment_constraint_label: string | null;
  policy_constraint_labels: Record<string, string> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  status_events?: { at: string; from: string | null; to: string; detail: string | null }[];
}

export interface MetricAgg {
  metric_key: string;
  group_key: string;
  unit: string;
  mean: number | null;
  std: number | null;
  ci_low: number | null;
  ci_high: number | null;
  ci_level: number | null;
  n_valid: number;
  n_total: number;
  status: string;
  reason: string | null;
}

export interface PolicyResult {
  label: string;
  key: string;
  version: string;
  params: Record<string, unknown>;
  resolution_notes: string[];
  flags: Record<string, number>;
  constraint_label: string;
  metrics: MetricAgg[];
}

export interface Results {
  experiment_id: string;
  status: string;
  calculation_version: string;
  replications: number;
  base_seed: number;
  baseline: string;
  experiment_constraint_label: string;
  warnings: string[];
  group_keys: string[];
  metric_catalog: Record<string, { unit: string; description: string }>;
  policies: PolicyResult[];
}

export interface PairedComparison {
  policy_label: string;
  baseline_label: string;
  metric_key: string;
  group_key: string;
  unit: string;
  n_pairs: number;
  mean_difference: number | null;
  ci_low: number | null;
  ci_high: number | null;
  ci_level: number | null;
  ci_excludes_zero: boolean | null;
}

export interface Comparison {
  baseline: string;
  difference_definition: string;
  equivalence: {
    comparable: boolean;
    checks: { name: string; ok: boolean; detail: string }[];
    workload_fingerprints: Record<string, string>;
  };
  comparisons: PairedComparison[];
}

export interface ConstraintRow {
  constraint_id: string;
  type: string;
  group_key: string;
  level: string;
  rule: string;
  status: "SATISFIED" | "VIOLATED" | "NOT_EVALUABLE";
  reason_code: string | null;
  certainty: string;
  observed: number | null;
  limit: number;
  unit: string;
  comparator: string;
  violation_magnitude: number | null;
  ci: number[] | null;
  ci_level: number | null;
  n_replications_valid: number;
  n_replications_total: number;
  replication_pass_rate: number | null;
  enforcement: { attempted_online: boolean; guarantee: string };
  explanation: string;
}

export interface Constraints {
  experiment_constraint_label: string;
  statement: string;
  policies: { policy_label: string; policy_key: string; constraint_label: string; constraints: ConstraintRow[] }[];
}

export interface Replications {
  items: {
    replication_index: number;
    seed_entropy: number;
    workload_fingerprint: string;
    visitor_count: number;
    status: string;
    error: string | null;
    policy_runs: { policy_label: string; headline_metrics: Record<string, number | null> }[];
  }[];
  total: number;
}

export interface Timeseries {
  policy_label: string;
  replication: number;
  counters: number;
  columns: string[];
  points: number[][];
  note: string;
}
