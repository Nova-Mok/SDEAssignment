import Link from "next/link";
import { dbExists, listScenarios, perScenarioSummary } from "../../lib/db";
import { fmtDelta, fmtMs, fmtNum } from "../../lib/format";

export const dynamic = "force-dynamic";

export default function ScenariosPage() {
  if (!dbExists()) {
    return (
      <div className="container">
        <h1>Scenarios</h1>
        <div className="empty-state">No benchmark data yet. Run the benchmark first.</div>
      </div>
    );
  }

  const scenarios = listScenarios();
  const summary = perScenarioSummary();

  return (
    <div className="container">
      <h1>Scenarios</h1>
      <p className="subtitle">Each scenario is a deterministic scripted turn, replayed against both configs.</p>

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>Scenario</th>
              <th>Runs (b / bc)</th>
              <th>Baseline P50</th>
              <th>Backchannel P50</th>
              <th>Delta P50</th>
              <th>Bad backchannels</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {scenarios.map((s) => {
              const configs = summary[s.scenario_id] ?? {};
              const b = configs["baseline"]?.response_latency_ms;
              const c = configs["backchannel"]?.response_latency_ms;
              const bad = configs["backchannel"]?.bad_backchannel_count;
              const delta = b?.p50 != null && c?.p50 != null ? c.p50 - b.p50 : null;
              const d = fmtDelta(delta);

              let status: { label: string; cls: string };
              if (!b || !c || b.n === 0 || c.n === 0) {
                status = { label: "no data", cls: "warn" };
              } else if (delta !== null && Math.abs(delta) > 100) {
                status = { label: "check", cls: "warn" };
              } else {
                status = { label: "ok", cls: "ok" };
              }

              return (
                <tr key={s.scenario_id} className="clickable">
                  <td style={{ textAlign: "left" }}>
                    <Link href={`/scenarios/${s.scenario_id}`}>{s.name}</Link>
                    <div className="small">{s.description}</div>
                  </td>
                  <td>{b?.n ?? 0} / {c?.n ?? 0}</td>
                  <td>{fmtMs(b?.p50 ?? null)}</td>
                  <td>{fmtMs(c?.p50 ?? null)}</td>
                  <td className={d.cls}>{d.text}</td>
                  <td>{fmtNum(bad?.mean ?? null, 2)}</td>
                  <td>
                    <span className={`badge ${status.cls}`}>{status.label}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
