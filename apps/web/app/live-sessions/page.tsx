import Link from "next/link";
import { listLiveSessions } from "../../lib/liveSessions";

export const dynamic = "force-dynamic";

export default function LiveSessionsPage() {
  const sessions = listLiveSessions();

  return (
    <div className="container">
      <h1>Live Sessions</h1>
      <p className="subtitle">
        Real conversations from the <Link href="/live">Live Demo</Link> page: a real LiveKit room, real AWS
        STT/LLM/TTS, real backchannel decisions. This is separate from the benchmark data on the other pages,
        which uses a scripted turn instead of a real microphone (see README &quot;Benchmark fairness&quot;).
      </p>

      {sessions.length === 0 ? (
        <div className="empty-state">
          No live sessions logged yet. Start <code>worker.py</code>, open <Link href="/live">/live</Link>, and
          have a conversation with backchannel mode on.
        </div>
      ) : (
        <div className="panel">
          <table>
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Room</th>
                <th style={{ textAlign: "left" }}>When</th>
                <th>Mode</th>
                <th>Turns</th>
                <th>Backchannels played</th>
                <th>Events</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.file} className="clickable">
                  <td style={{ textAlign: "left" }}>
                    <Link href={`/live-sessions/${encodeURIComponent(s.file)}`}>{s.roomName}</Link>
                  </td>
                  <td style={{ textAlign: "left" }}>
                    {s.startedAt ? new Date(s.startedAt * 1000).toLocaleString() : s.timestamp}
                  </td>
                  <td>
                    <span className={`badge ${s.mode === "backchannel" ? "ok" : "warn"}`}>{s.mode}</span>
                  </td>
                  <td>{s.turnCount}</td>
                  <td>{s.backchannelCount}</td>
                  <td>{s.eventCount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
