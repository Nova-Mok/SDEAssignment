import Link from "next/link";
import Timeline from "../../../components/Timeline";
import { findComparableRun, getEventsForRun, getRun } from "../../../lib/db";
import { fmtMs } from "../../../lib/format";

export const dynamic = "force-dynamic";

export default async function RunDetailPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const run = getRun(runId);
  const events = getEventsForRun(runId);

  if (!run) {
    return (
      <div className="container">
        <div className="empty-state">Run {runId} not found.</div>
      </div>
    );
  }

  const otherConfig = run.config === "baseline" ? "backchannel" : "baseline";
  const comparable = findComparableRun(run.scenario_id, otherConfig, run.run_index);

  return (
    <div className="container">
      <p className="small">
        <Link href={`/scenarios/${run.scenario_id}`}>&larr; {run.scenario_id}</Link>
      </p>
      <h1>
        Run detail <span className={`badge ${run.config === "backchannel" ? "ok" : "warn"}`}>{run.config}</span>
      </h1>
      <p className="subtitle">
        {run.run_id}, run #{run.run_index}
        {comparable && (
          <>
            {" · "}
            <Link href={`/runs/${comparable.run_id}`}>compare with {otherConfig} run #{run.run_index}</Link>
          </>
        )}
      </p>

      <div className="panel" style={{ marginBottom: 20, overflowX: "auto" }}>
        <Timeline events={events} />
      </div>

      <div className="grid-2">
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Latency</h2>
          <table>
            <tbody>
              <tr><td>Response latency</td><td>{fmtMs(run.response_latency_ms)}</td></tr>
              <tr><td>LLM TTFT</td><td>{fmtMs(run.llm_ttft_ms)}</td></tr>
              <tr><td>TTS first audio</td><td>{fmtMs(run.tts_first_audio_ms)}</td></tr>
              <tr><td>Backchannel latency (avg)</td><td>{fmtMs(run.backchannel_latency_ms)}</td></tr>
              <tr><td>STT finalization (scripted)</td><td>{fmtMs(run.stt_finalization_ms)}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Behaviour</h2>
          <table>
            <tbody>
              <tr><td>Backchannels played</td><td>{run.backchannel_count}</td></tr>
              <tr><td>Cancelled</td><td>{run.cancelled_count}</td></tr>
              <tr><td>Suppressed candidates</td><td>{run.suppressed_count}</td></tr>
              <tr><td>Bad backchannels</td><td>{run.bad_backchannel_count}</td></tr>
              <tr><td>User/agent overlap</td><td>{fmtMs(run.overlap_ms)}</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <h2>Raw event log</h2>
      <div className="panel">
        <table>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>t (s)</th>
              <th style={{ textAlign: "left" }}>event</th>
              <th style={{ textAlign: "left" }}>source</th>
              <th style={{ textAlign: "left" }}>metadata</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td style={{ textAlign: "left" }}>{(e.timestamp - events[0].timestamp).toFixed(3)}</td>
                <td style={{ textAlign: "left" }}>{e.event_type}</td>
                <td style={{ textAlign: "left" }}>{e.source}</td>
                <td style={{ textAlign: "left" }}><code>{e.metadata_json}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
