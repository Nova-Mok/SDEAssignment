import Link from "next/link";
import Timeline from "../../../components/Timeline";
import { getLiveSessionEvents, listLiveSessions } from "../../../lib/liveSessions";

export const dynamic = "force-dynamic";

export default async function LiveSessionDetailPage({ params }: { params: Promise<{ file: string }> }) {
  const { file: encodedFile } = await params;
  const file = decodeURIComponent(encodedFile);
  const events = getLiveSessionEvents(file);
  const summary = listLiveSessions().find((s) => s.file === file);

  if (events.length === 0) {
    return (
      <div className="container">
        <div className="empty-state">Session not found or empty.</div>
      </div>
    );
  }

  const backchannels = events.filter((e) => e.event_type === "BACKCHANNEL_AUDIO_END");
  const cancelled = events.filter((e) => e.event_type === "BACKCHANNEL_CANCELLED");
  const suppressed = events.filter((e) => e.event_type === "BACKCHANNEL_SUPPRESSED");
  const badBackchannels = events.filter((e) => e.event_type === "BAD_BACKCHANNEL");
  const errors = events.filter((e) => e.event_type === "ERROR");
  const turns = events.filter((e) => e.event_type === "USER_SPEECH_START");

  return (
    <div className="container">
      <p className="small">
        <Link href="/live-sessions">&larr; Live Sessions</Link>
      </p>
      <h1>
        {summary?.roomName ?? file} <span className={`badge ${summary?.mode === "backchannel" ? "ok" : "warn"}`}>{summary?.mode}</span>
      </h1>
      <p className="subtitle">
        Real conversation, real STT/LLM/TTS, real backchannel engine. Not benchmark data.
      </p>

      <div className="panel" style={{ marginBottom: 20, overflowX: "auto" }}>
        <Timeline events={events} />
      </div>

      <div className="grid-2">
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Session summary</h2>
          <table>
            <tbody>
              <tr><td>User turns</td><td>{turns.length}</td></tr>
              <tr><td>Backchannels played</td><td>{backchannels.length}</td></tr>
              <tr><td>Cancelled</td><td>{cancelled.length}</td></tr>
              <tr><td>Suppressed candidates</td><td>{suppressed.length}</td></tr>
              <tr><td>Bad backchannels</td><td>{badBackchannels.length}</td></tr>
              <tr><td>Errors</td><td>{errors.length}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Backchannels played</h2>
          {backchannels.length === 0 ? (
            <div className="small">None in this session.</div>
          ) : (
            <table>
              <thead>
                <tr><th style={{ textAlign: "left" }}>Phrase</th><th>t (s)</th></tr>
              </thead>
              <tbody>
                {backchannels.map((e, i) => {
                  const meta = JSON.parse(e.metadata_json);
                  return (
                    <tr key={i}>
                      <td style={{ textAlign: "left" }}>{meta.phrase ?? "?"}</td>
                      <td>{(e.timestamp - events[0].timestamp).toFixed(1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
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
