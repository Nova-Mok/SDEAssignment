import { describe, expect, it } from "vitest";
import { summarize } from "./db";

// Pure-logic tests for the same P50/P95 math the dashboard renders and
// benchmarks/aggregate.py computes independently on the Python side,
// both implementations exist because the writer (Python) and the reader
// (this dashboard) are different languages, not because the math differs.

describe("summarize", () => {
  it("returns an all-null summary for an empty input", () => {
    const s = summarize([]);
    expect(s).toEqual({ n: 0, mean: null, p50: null, p95: null, min: null, max: null });
  });

  it("filters out null/undefined before summarizing", () => {
    const s = summarize([10, null, 20, undefined, 30]);
    expect(s.n).toBe(3);
    expect(s.mean).toBe(20);
  });

  it("computes mean/min/max correctly", () => {
    const s = summarize([1, 2, 3, 4, 5]);
    expect(s.mean).toBe(3);
    expect(s.min).toBe(1);
    expect(s.max).toBe(5);
  });

  it("p50 matches the median for an odd-length sorted set", () => {
    const s = summarize([5, 1, 3, 2, 4]);
    expect(s.p50).toBe(3);
  });

  it("p95 is close to the max for a small sample and never exceeds it", () => {
    const values = Array.from({ length: 20 }, (_, i) => i + 1); // 1..20
    const s = summarize(values);
    expect(s.p95).toBeLessThanOrEqual(20);
    expect(s.p95).toBeGreaterThan(s.p50 as number);
  });

  it("single-value input has p50 == p95 == that value", () => {
    const s = summarize([42]);
    expect(s.p50).toBe(42);
    expect(s.p95).toBe(42);
    expect(s.mean).toBe(42);
  });
});
