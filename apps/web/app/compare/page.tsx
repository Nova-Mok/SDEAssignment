import Link from "next/link";
import Timeline from "../../components/Timeline";
import { dbExists, findComparableRun, getEventsForRun, listRuns, listScenarios } from "../../lib/db";
import { fmtDelta, fmtMs } from "../../lib/format";

export const dynamic = "force-dynamic";

export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<{ scenario?: string; run?: string }>;
}) {
  if (!dbExists()) {
    return (
      <div className="container">
        <h1>Compare</h1>
        <div className="empty-state">No benchmark data yet.</div>
      </div>
    );
  }

  const sp = await searchParams;
  const scenarios = listScenarios();
  const scenarioId = sp.scenario ?? scenarios[0]?.scenario_id;
  const runIndex = sp.run ? parseInt(sp.run, 10) : 0;

  const availableRunIndexes = Array.from(
    new Set(listRuns({ scenarioId }).map((r) => r.run_index))
  ).sort((a, b) => a - b);

  const baseline = findComparableRun(scenarioId, "baseline", runIndex);
  const backchannel = findComparableRun(scenarioId, "backchannel", runIndex);
  const baselineEvents = baseline ? getEventsForRun(baseline.run_id) : [];
  const backchannelEvents = backchannel ? getEventsForRun(backchannel.run_id) : [];

  const delta =
    baseline?.response_latency_ms != null && backchannel?.response_latency_ms != null
      ? backchannel.response_latency_ms - baseline.response_latency_ms
      : null;
  const d = fmtDelta(delta);

  return (
    <div className="container">
      <h1>Compare</h1>
      <p className="subtitle">Same scenario, same run index, baseline vs backchannel. Everything else held constant.</p>

      <div className="panel" style={{ marginBottom: 20 }}>
        <form style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <select name="scenario" defaultValue={scenarioId} style={{ background: "#0a0c10", color: "inherit", border: "1px solid #232838", borderRadius: 6, padding: 6 }}>
            {scenarios.map((s) => (
              <option key={s.scenario_id} value={s.scenario_id}>{s.name}</option>
            ))}
          </select>
          <select name="run" defaultValue={String(runIndex)} style={{ background: "#0a0c10", color: "inherit", border: "1px solid #232838", borderRadius: 6, padding: 6 }}>
            {availableRunIndexes.map((i) => (
              <option key={i} value={i}>run #{i}</option>
            ))}
          </select>
          <button className="btn" type="submit">Load</button>
        </form>
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        Response latency: baseline {fmtMs(baseline?.response_latency_ms ?? null)} vs backchannel {fmtMs(backchannel?.response_latency_ms ?? null)}
        {" ("}<span className={d.cls}>{d.text}</span>{")"}
      </div>

      <h2>Baseline {baseline && <Link href={`/runs/${baseline.run_id}`} className="small">(full detail)</Link>}</h2>
      <div className="panel" style={{ marginBottom: 20, overflowX: "auto" }}>
        <Timeline events={baselineEvents} />
      </div>

      <h2>Backchannel {backchannel && <Link href={`/runs/${backchannel.run_id}`} className="small">(full detail)</Link>}</h2>
      <div className="panel" style={{ overflowX: "auto" }}>
        <Timeline events={backchannelEvents} />
      </div>
    </div>
  );
}
