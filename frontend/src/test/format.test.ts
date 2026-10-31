import { ci, humanize, metricValue, num, when } from "../format";

describe("format", () => {
  it("formats metric values by unit and handles missing values", () => {
    expect(metricValue(0.8123, "proportion")).toBe("81.2%");
    expect(metricValue(3.456, "minute")).toBe("3.46 min");
    expect(metricValue(null, "minute")).toBe("—");
    expect(num(undefined)).toBe("—");
    expect(num(Number.NaN)).toBe("—");
  });
  it("formats intervals and says so when none exists", () => {
    expect(ci(1, 2, "minute")).toBe("[1 min, 2 min]");
    expect(ci(null, 2, "minute")).toBe("no interval");
  });
  it("humanizes codes and dates", () => {
    expect(humanize("NOT_EVALUABLE")).toBe("Not evaluable");
    expect(humanize(null)).toBe("");
    expect(when(null)).toBe("—");
    expect(when("garbage")).toBe("garbage");
  });
});
