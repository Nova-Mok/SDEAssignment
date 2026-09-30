import { dbExists, overallSummary } from "../../lib/db";
import { fmtDelta, fmtMs, fmtNum } from "../../lib/format";

export const dynamic = "force-dynamic";

function Row({
  label,
  baseline,
  backchannel,
  unit = "ms",
  digits = 0,
}: {
  label: string;
  baseline: number | null;
  backchannel: number | null;
  unit?: string;
  digits?: number;
}) {
  const delta =
    baseline !== null && backchannel !== null ? backchannel - baseline : null;
  const d = fmtDelta(delta, unit);
  return (
    <tr>
      <td>{label}</td>
      <td>{unit === "ms" ? fmtMs(baseline) : fmtNum(baseline, digits)}</td>
      <td>{unit === "ms" ? fmtMs(backchannel) : fmtNum(backchannel, digits)}</td>
      <td className={d.cls}>{d.text}</td>
    </tr>
  );
}

export default function OverviewPage() {
  if (!dbExists()) {
    return (
      <div className="container">
        <h1>Overview</h1>
        <p className="subtitle">Baseline vs backchannel, pooled across all scenarios</p>
        <div className="empty-state">
          No benchmark data yet. Run <code>python -m benchmarks.cli run</code> from the repo root.
        </div>
      </div>
    );
  }

  const summary = overallSummary();
  const baseline = summary["baseline"];
  const backchannel = summary["backchannel"];

  const nBaseline = baseline?.response_latency_ms.n ?? 0;
  const nBackchannel = backchannel?.response_latency_ms.n ?? 0;

  return (
    <div className="container">
      <h1>Overview</h1>
      <p className="subtitle">
        Baseline vs backchannel, pooled across all scenarios ({nBaseline} baseline runs, {nBackchannel} backchannel runs).
        This is the number that answers: <strong>did backchanneling make the agent&apos;s real response slower?</strong>
      </p>

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>Metric</th>
              <th>Baseline</th>
              <th>Backchannel</th>
              <th>Delta</th>
            </tr>
          </thead>
          <tbody>
            <Row label="Response latency (P50)" baseline={baseline?.response_latency_ms.p50 ?? null} backchannel={backchannel?.response_latency_ms.p50 ?? null} />
            <Row label="Response latency (P95)" baseline={baseline?.response_latency_ms.p95 ?? null} backchannel={backchannel?.response_latency_ms.p95 ?? null} />
            <Row label="LLM time-to-first-token (P50)" baseline={baseline?.llm_ttft_ms.p50 ?? null} backchannel={backchannel?.llm_ttft_ms.p50 ?? null} />
            <Row label="LLM time-to-first-token (P95)" baseline={baseline?.llm_ttft_ms.p95 ?? null} backchannel={backchannel?.llm_ttft_ms.p95 ?? null} />
            <Row label="TTS time-to-first-audio (P50)" baseline={baseline?.tts_first_audio_ms.p50 ?? null} backchannel={backchannel?.tts_first_audio_ms.p50 ?? null} />
            <Row label="TTS time-to-first-audio (P95)" baseline={baseline?.tts_first_audio_ms.p95 ?? null} backchannel={backchannel?.tts_first_audio_ms.p95 ?? null} />
          </tbody>
        </table>
      </div>

      <h2>Backchannel quality (backchannel config only)</h2>
      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>Metric</th>
              <th>Mean</th>
              <th>P50</th>
              <th>Max</th>
              <th>n</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Backchannel decision → audible (latency)</td>
              <td>{fmtMs(backchannel?.backchannel_latency_ms.mean ?? null)}</td>
              <td>{fmtMs(backchannel?.backchannel_latency_ms.p50 ?? null)}</td>
              <td>{fmtMs(backchannel?.backchannel_latency_ms.max ?? null)}</td>
              <td>{backchannel?.backchannel_latency_ms.n ?? 0}</td>
            </tr>
            <tr>
              <td>Backchannels per run</td>
              <td>{fmtNum(backchannel?.backchannel_count.mean ?? null, 2)}</td>
              <td>{fmtNum(backchannel?.backchannel_count.p50 ?? null, 0)}</td>
              <td>{fmtNum(backchannel?.backchannel_count.max ?? null, 0)}</td>
              <td>{backchannel?.backchannel_count.n ?? 0}</td>
            </tr>
            <tr>
              <td>Cancelled backchannels per run</td>
              <td>{fmtNum(backchannel?.cancelled_count.mean ?? null, 2)}</td>
              <td>{fmtNum(backchannel?.cancelled_count.p50 ?? null, 0)}</td>
              <td>{fmtNum(backchannel?.cancelled_count.max ?? null, 0)}</td>
              <td>{backchannel?.cancelled_count.n ?? 0}</td>
            </tr>
            <tr>
              <td>Suppressed candidates per run</td>
              <td>{fmtNum(backchannel?.suppressed_count.mean ?? null, 2)}</td>
              <td>{fmtNum(backchannel?.suppressed_count.p50 ?? null, 0)}</td>
              <td>{fmtNum(backchannel?.suppressed_count.max ?? null, 0)}</td>
              <td>{backchannel?.suppressed_count.n ?? 0}</td>
            </tr>
            <tr>
              <td>Bad backchannels per run (see definition in README)</td>
              <td>{fmtNum(backchannel?.bad_backchannel_count.mean ?? null, 2)}</td>
              <td>{fmtNum(backchannel?.bad_backchannel_count.p50 ?? null, 0)}</td>
              <td>{fmtNum(backchannel?.bad_backchannel_count.max ?? null, 0)}</td>
              <td>{backchannel?.bad_backchannel_count.n ?? 0}</td>
            </tr>
            <tr>
              <td>User/agent overlap caused by a backchannel</td>
              <td>{fmtMs(backchannel?.overlap_ms.mean ?? null)}</td>
              <td>{fmtMs(backchannel?.overlap_ms.p50 ?? null)}</td>
              <td>{fmtMs(backchannel?.overlap_ms.max ?? null)}</td>
              <td>{backchannel?.overlap_ms.n ?? 0}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p className="small" style={{ marginTop: 20 }}>
        Scripted: user speech timing/content. Real, measured: LLM call (AWS Bedrock), TTS call (Amazon Polly),
        and backchannel scheduling. No live LiveKit room in these numbers, see README &quot;Benchmark fairness&quot;.
      </p>
    </div>
  );
}
