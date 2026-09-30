import Database from "better-sqlite3";
import path from "path";
import type { EventRow, MetricSummary, RunRow, ScenarioRow } from "./types";

// Same file the Python benchmark runner and the live agent's SqliteRecorder
// write to. Read-only from here. The dashboard never mutates this DB.
const DB_PATH = path.resolve(process.cwd(), "../../data/benchmark.sqlite");

let _db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (!_db) {
    _db = new Database(DB_PATH, { readonly: true, fileMustExist: false });
  }
  return _db;
}

export function dbExists(): boolean {
  try {
    const db = getDb();
    db.prepare("SELECT 1 FROM runs LIMIT 1").get();
    return true;
  } catch {
    return false;
  }
}

export function listScenarios(): ScenarioRow[] {
  try {
    return getDb().prepare("SELECT * FROM scenarios ORDER BY scenario_id").all() as ScenarioRow[];
  } catch {
    return [];
  }
}

export function listRuns(filters: { scenarioId?: string; config?: string } = {}): RunRow[] {
  try {
    let sql = "SELECT * FROM runs";
    const clauses: string[] = [];
    const params: Record<string, string> = {};
    if (filters.scenarioId) {
      clauses.push("scenario_id = @scenarioId");
      params.scenarioId = filters.scenarioId;
    }
    if (filters.config) {
      clauses.push("config = @config");
      params.config = filters.config;
    }
    if (clauses.length) sql += " WHERE " + clauses.join(" AND ");
    sql += " ORDER BY started_at DESC";
    return getDb().prepare(sql).all(params) as RunRow[];
  } catch {
    return [];
  }
}

export function getRun(runId: string): RunRow | undefined {
  try {
    return getDb().prepare("SELECT * FROM runs WHERE run_id = ?").get(runId) as RunRow | undefined;
  } catch {
    return undefined;
  }
}

export function getEventsForRun(runId: string): EventRow[] {
  try {
    return getDb()
      .prepare("SELECT * FROM events WHERE run_id = ? ORDER BY id")
      .all(runId) as EventRow[];
  } catch {
    return [];
  }
}

export function findComparableRun(scenarioId: string, config: string, runIndex: number): RunRow | undefined {
  try {
    return getDb()
      .prepare("SELECT * FROM runs WHERE scenario_id = ? AND config = ? AND run_index = ? ORDER BY started_at DESC LIMIT 1")
      .get(scenarioId, config, runIndex) as RunRow | undefined;
  } catch {
    return undefined;
  }
}

function percentile(sorted: number[], p: number): number | null {
  if (sorted.length === 0) return null;
  const k = (sorted.length - 1) * p;
  const f = Math.floor(k);
  const c = Math.min(f + 1, sorted.length - 1);
  if (f === c) return sorted[f];
  return sorted[f] + (sorted[c] - sorted[f]) * (k - f);
}

export function summarize(valuesIn: (number | null | undefined)[]): MetricSummary {
  const values = valuesIn.filter((v): v is number => v !== null && v !== undefined);
  if (values.length === 0) {
    return { n: 0, mean: null, p50: null, p95: null, min: null, max: null };
  }
  const sorted = [...values].sort((a, b) => a - b);
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  return {
    n: values.length,
    mean,
    p50: percentile(sorted, 0.5),
    p95: percentile(sorted, 0.95),
    min: sorted[0],
    max: sorted[sorted.length - 1],
  };
}

export const METRIC_COLUMNS = [
  "response_latency_ms",
  "llm_ttft_ms",
  "tts_first_audio_ms",
  "backchannel_latency_ms",
  "backchannel_count",
  "cancelled_count",
  "bad_backchannel_count",
  "suppressed_count",
  "overlap_ms",
] as const;
export type MetricColumn = (typeof METRIC_COLUMNS)[number];

function emptyMetricBuckets(): Record<MetricColumn, (number | null)[]> {
  const out = {} as Record<MetricColumn, (number | null)[]>;
  for (const m of METRIC_COLUMNS) out[m] = [];
  return out;
}

export function overallSummary(): Record<string, Record<MetricColumn, MetricSummary>> {
  const runs = listRuns();
  const byConfig: Record<string, Record<MetricColumn, (number | null)[]>> = {};
  for (const r of runs) {
    const bucket = (byConfig[r.config] ??= emptyMetricBuckets());
    for (const m of METRIC_COLUMNS) bucket[m].push(r[m] as number | null);
  }
  const out: Record<string, Record<MetricColumn, MetricSummary>> = {};
  for (const [config, metrics] of Object.entries(byConfig)) {
    out[config] = Object.fromEntries(
      METRIC_COLUMNS.map((m) => [m, summarize(metrics[m])])
    ) as Record<MetricColumn, MetricSummary>;
  }
  return out;
}

export function perScenarioSummary(): Record<string, Record<string, Record<MetricColumn, MetricSummary>>> {
  const runs = listRuns();
  const nested: Record<string, Record<string, Record<MetricColumn, (number | null)[]>>> = {};
  for (const r of runs) {
    const scenarioBucket = (nested[r.scenario_id] ??= {});
    const configBucket = (scenarioBucket[r.config] ??= emptyMetricBuckets());
    for (const m of METRIC_COLUMNS) configBucket[m].push(r[m] as number | null);
  }
  const out: Record<string, Record<string, Record<MetricColumn, MetricSummary>>> = {};
  for (const [scenarioId, configs] of Object.entries(nested)) {
    out[scenarioId] = {};
    for (const [config, metrics] of Object.entries(configs)) {
      out[scenarioId][config] = Object.fromEntries(
        METRIC_COLUMNS.map((m) => [m, summarize(metrics[m])])
      ) as Record<MetricColumn, MetricSummary>;
    }
  }
  return out;
}
