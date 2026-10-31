import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExperimentDetail } from "../pages/ExperimentDetail";
import { Experiments } from "../pages/Experiments";
import { NewExperiment } from "../pages/NewExperiment";
import { ScenarioEditor } from "../pages/ScenarioEditor";
import { Scenarios } from "../pages/Scenarios";
import comparison from "./fixtures/comparison.json";
import constraints from "./fixtures/constraints.json";
import demos from "./fixtures/demos.json";
import experiment from "./fixtures/experiment.json";
import experiments from "./fixtures/experiments.json";
import metadata from "./fixtures/metadata.json";
import policies from "./fixtures/policies.json";
import replications from "./fixtures/replications.json";
import results from "./fixtures/results.json";
import scenario from "./fixtures/scenario.json";
import timeseries from "./fixtures/timeseries.json";
import { mockApi, renderAt } from "./helpers";

// Fixtures are real responses captured from the running backend (see README: "Frontend tests").
const EID = experiment.id;
const detailRoutes = [
  { match: /\/experiments\/[^/?]+\?include_events/, body: experiment },
  { match: "/results", body: results },
  { match: "/constraints", body: constraints },
  { match: "/comparison", body: comparison },
  { match: "/replications", body: replications },
  { match: "/timeseries", body: timeseries },
];

describe("ExperimentDetail", () => {
  it("shows the experiment verdict, metrics, and constraint explanations from the backend", async () => {
    mockApi(detailRoutes);
    renderAt(<ExperimentDetail />, `/experiments/${EID}`, "/experiments/:id");
    expect(await screen.findByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(await screen.findByText(/Headline metrics/)).toBeInTheDocument();
    expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("tab", { name: "Fairness" }));
    expect(await screen.findByText(/No policy is claimed to guarantee an aggregate constraint/)).toBeInTheDocument();
    const first = constraints.policies[0]!.constraints[0]!;
    expect((await screen.findAllByText(new RegExp(first.constraint_id))).length).toBeGreaterThan(0);
  });

  it("explains reproducibility details and offers exports", async () => {
    mockApi(detailRoutes);
    renderAt(<ExperimentDetail />, `/experiments/${EID}`, "/experiments/:id");
    await userEvent.click(await screen.findByRole("tab", { name: "Reproducibility" }));
    expect(await screen.findByText(experiment.scenario_config_hash)).toBeInTheDocument();
    expect(screen.getByText(String(experiment.base_seed))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Manifest/ })).toHaveAttribute("href", expect.stringContaining("kind=manifest"));
  });

  it("shows the policy comparison with the workload-equivalence checks", async () => {
    mockApi(detailRoutes);
    renderAt(<ExperimentDetail />, `/experiments/${EID}`, "/experiments/:id");
    await userEvent.click(await screen.findByRole("tab", { name: "Policy comparison" }));
    expect(await screen.findByText(/equivalent workloads/i)).toBeInTheDocument();
  });

  it("renders a failed experiment with its error and a retry action, and no results", async () => {
    const failed = { ...experiment, status: "FAILED", error_code: "CHILD_CRASHED", error_summary: "The simulation process exited unexpectedly (exit code 7).", experiment_constraint_label: null };
    const calls = mockApi([
      { match: /\/experiments\/[^/?]+\?include_events/, body: failed },
      { match: "/replications", body: { items: [], total: 0, limit: 100, offset: 0 } },
      { match: "/retry", method: "POST", status: 202, body: { ...failed, id: "new-id", status: "QUEUED" } },
    ]);
    renderAt(<ExperimentDetail />, `/experiments/${EID}`, "/experiments/:id");
    expect(await screen.findByRole("alert")).toHaveTextContent(/exit code 7/);
    expect(screen.queryByRole("tab", { name: "Overview" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/retry"))).toBe(true));
  });

  it("shows progress for a running experiment", async () => {
    mockApi([{ match: /\/experiments\/[^/?]+\?include_events/, body: { ...experiment, status: "RUNNING", replications_done: 3, replications_total: 10, experiment_constraint_label: null } }]);
    renderAt(<ExperimentDetail />, `/experiments/${EID}`, "/experiments/:id");
    const bar = await screen.findByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "30");
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("shows an error state when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("down")));
    renderAt(<ExperimentDetail />, `/experiments/${EID}`, "/experiments/:id");
    expect(await screen.findByText("Backend unreachable")).toBeInTheDocument();
  });
});

describe("lists", () => {
  it("shows the empty state when there are no experiments", async () => {
    mockApi([{ match: "/experiments", body: { items: [], total: 0, limit: 50, offset: 0 } }]);
    renderAt(<Experiments />, "/experiments");
    expect(await screen.findByText("No experiments yet")).toBeInTheDocument();
  });
  it("lists experiments with status badges", async () => {
    mockApi([{ match: "/experiments", body: experiments }]);
    renderAt(<Experiments />, "/experiments");
    expect(await screen.findByText(experiments.items[0]!.scenario_name)).toBeInTheDocument();
  });
  it("shows the empty state for scenarios and offers templates", async () => {
    mockApi([
      { match: "/scenarios", body: { items: [], total: 0, limit: 50, offset: 0 } },
      { match: "/demo-scenarios", body: demos },
    ]);
    renderAt(<Scenarios />, "/");
    expect(await screen.findByText("No scenarios yet")).toBeInTheDocument();
    expect(await screen.findByText(demos[0]!.name)).toBeInTheDocument();
  });
});

describe("ScenarioEditor", () => {
  const base = [
    { match: "/demo-scenarios", body: demos },
    { match: "/metadata", body: metadata },
    { match: /\/scenarios\/[^/]+$/, body: scenario },
  ];
  it("surfaces backend validation errors and blocks saving an invalid configuration", async () => {
    mockApi([
      ...base,
      {
        match: "/scenario-validations",
        method: "POST",
        body: { valid: false, errors: [{ path: "config.counters", code: "greater_than_equal", message: "Input should be greater than or equal to 1" }], warnings: [], estimated_cost: null, config_hash: null },
      },
    ]);
    renderAt(<ScenarioEditor />, `/scenarios/${scenario.id}`, "/scenarios/:id");
    expect(await screen.findByText("This configuration is not valid yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save new version/ })).toBeDisabled();
  });
  it("lets the user edit a field and enables saving once the backend says it is valid", async () => {
    const calls = mockApi([
      ...base,
      { match: "/scenario-validations", method: "POST", body: { valid: true, errors: [], warnings: [], estimated_cost: { expected_visitors_per_run: 100, replications: 10, policies: 1, visitor_simulations: 1000 }, config_hash: "sha256:" + "a".repeat(64) } },
      { match: /\/scenarios\/[^/]+$/, method: "PATCH", body: scenario },
    ]);
    renderAt(<ScenarioEditor />, `/scenarios/${scenario.id}`, "/scenarios/:id");
    const counters = await screen.findByRole("spinbutton", { name: /^Counters/ });
    await userEvent.clear(counters);
    await userEvent.type(counters, "4");
    const save = screen.getByRole("button", { name: /Save new version/ });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.init?.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.init?.method === "PATCH")!;
    expect(JSON.parse(String(patch.init?.body)).config.counters).toBe(4);
  });
  it("starts a new scenario from a backend template", async () => {
    mockApi(base);
    renderAt(<ScenarioEditor />, "/scenarios/new", "/scenarios/new");
    expect((await screen.findAllByText("Use this template")).length).toBe(demos.length);
  });
});

describe("NewExperiment", () => {
  const routes = [
    { match: /\/scenarios\?/, body: { items: [scenario], total: 1, limit: 100, offset: 0 } },
    { match: "/policies", body: policies },
  ];
  it("requires parameters of the aging policy before submit is possible", async () => {
    mockApi(routes);
    renderAt(<NewExperiment />, "/experiments/new");
    const aging = await screen.findByLabelText(/Aging priority/);
    await userEvent.click(aging);
    expect(await screen.findByText(/Required parameters missing/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run 3 polic/ })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/aging_rate_per_minute/), "0.05");
    await waitFor(() => expect(screen.getByRole("button", { name: /Run 3 polic/ })).toBeEnabled());
  });
  it("submits the selected policies with an idempotency key and shows API validation errors", async () => {
    const calls = mockApi([
      ...routes,
      { match: "/experiments", method: "POST", status: 422, body: { error: { code: "VALIDATION_ERROR", message: "The request is invalid.", field_errors: [{ path: "policies", code: "x", message: "nope" }] } } },
    ]);
    renderAt(<NewExperiment />, "/experiments/new");
    const run = await screen.findByRole("button", { name: /Run 2 polic/ });
    await userEvent.click(run);
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/nope/)).toBeInTheDocument();
    const post = calls.find((c) => c.init?.method === "POST")!;
    expect((post.init?.headers as Record<string, string>)["Idempotency-Key"]).toMatch(/[0-9a-f-]{36}/);
    expect(JSON.parse(String(post.init?.body)).policies.map((p: { key: string }) => p.key)).toEqual(["fcfs", "simuloom_fair"]);
  });
});
