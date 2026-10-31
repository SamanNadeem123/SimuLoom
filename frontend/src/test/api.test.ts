import { api, ApiError } from "../api";
import { mockApi } from "./helpers";

describe("api client", () => {
  it("turns structured backend errors into ApiError with field errors", async () => {
    mockApi([
      {
        match: "/scenarios",
        method: "POST",
        status: 422,
        body: { error: { code: "VALIDATION_ERROR", message: "bad", request_id: "r1", field_errors: [{ path: "config.counters", code: "x", message: "too small" }] } },
      },
    ]);
    const err = await api.createScenario("a", "", {} as never).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const e = err as ApiError;
    expect(e.status).toBe(422);
    expect(e.code).toBe("VALIDATION_ERROR");
    expect(e.fieldErrors[0]?.path).toBe("config.counters");
    expect(e.requestId).toBe("r1");
  });
  it("reports an unreachable backend distinctly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fail")));
    const e = (await api.metadata().catch((x: unknown) => x)) as ApiError;
    expect(e.status).toBe(0);
    expect(e.code).toBe("NETWORK_ERROR");
  });
  it("sends the idempotency key header and handles 204", async () => {
    const calls = mockApi([
      { match: "/experiments", method: "POST", status: 202, body: { id: "x" } },
      { match: "/scenarios/abc", method: "DELETE", status: 204 },
    ]);
    await api.submitExperiment({ scenario_id: "s", policies: [{ key: "fcfs", params: {} }] }, "key-123");
    expect((calls[0]?.init?.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-123");
    await expect(api.archiveScenario("abc")).resolves.toBeUndefined();
  });
});
