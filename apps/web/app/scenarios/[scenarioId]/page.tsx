import Link from "next/link";
import { listRuns, listScenarios } from "../../../lib/db";
import { fmtMs } from "../../../lib/format";

export const dynamic = "force-dynamic";

export default async function ScenarioDetailPage({
  params,
}: {
  params: Promise<{ scenarioId: string }>;
}) {
  const { scenarioId } = await params;
  const scenario = listScenarios().find((s) => s.scenario_id === scenarioId);
  const runs = listRuns({ scenarioId });

  return (
    <div className="container">
      <p className="small">
        <Link href="/scenarios">&larr; Scenarios</Link>
      </p>
      <h1>{scenario?.name ?? scenarioId}</h1>
      <p className="subtitle">{scenario?.description}</p>
      {scenario && (
        <div className="panel" style={{ marginBottom: 20 }}>
          <strong>Expected behaviour:</strong> {scenario.expected_behaviour}
        </div>
      )}

      <h2>Runs ({runs.length})</h2>
      <div className="panel">
        <table>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>Run</th>
              <th>Config</th>
              <th>#</th>
              <th>Response latency</th>
              <th>LLM TTFT</th>
              <th>TTS first audio</th>
              <th>Backchannels</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id} className="clickable">
                <td style={{ textAlign: "left" }}>
                  <Link href={`/runs/${r.run_id}`}>{r.run_id.split("__")[0]}…</Link>
                </td>
                <td>
                  <span className={`badge ${r.config === "backchannel" ? "ok" : "warn"}`}>{r.config}</span>
                </td>
                <td>{r.run_index}</td>
                <td>{fmtMs(r.response_latency_ms)}</td>
                <td>{fmtMs(r.llm_ttft_ms)}</td>
                <td>{fmtMs(r.tts_first_audio_ms)}</td>
                <td>{r.backchannel_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
