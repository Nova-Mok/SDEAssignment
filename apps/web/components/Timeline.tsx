import type { EventRow } from "../lib/types";

// Hand-rolled SVG timeline, matches the assignment's ASCII mockup: USER /
// STT / EOT / BACKCHANNEL / AGENT lanes, positioned by real event
// timestamps. No charting library; this is simple enough that one would
// add more moving parts than it removes.

const LANE_HEIGHT = 46;
const LABEL_WIDTH = 110;
const PADDING = 16;

function parseMeta(json: string): Record<string, unknown> {
  try {
    return JSON.parse(json);
  } catch {
    return {};
  }
}

function findFirst(events: EventRow[], type: string) {
  return events.find((e) => e.event_type === type);
}
function findAll(events: EventRow[], type: string) {
  return events.filter((e) => e.event_type === type);
}

export default function Timeline({ events }: { events: EventRow[] }) {
  if (events.length === 0) {
    return <div className="empty-state">No events recorded for this run.</div>;
  }

  const t0 = events[0].timestamp;
  const tEnd = events[events.length - 1].timestamp;
  const durationS = Math.max(0.5, tEnd - t0);
  const widthPx = 760;
  const x = (ts: number) => LABEL_WIDTH + ((ts - t0) / durationS) * widthPx;

  const userStart = findFirst(events, "USER_SPEECH_START");
  const userEnd = findFirst(events, "USER_SPEECH_END");
  const stt = events.filter((e) => e.event_type === "STT_INTERIM" || e.event_type === "STT_FINAL");
  const eotUpdates = findAll(events, "EOT_PROBABILITY_UPDATED");
  const selected = findAll(events, "BACKCHANNEL_SELECTED");
  const suppressed = findAll(events, "BACKCHANNEL_SUPPRESSED");
  const cancelled = findAll(events, "BACKCHANNEL_CANCELLED");
  const audioStart = findAll(events, "BACKCHANNEL_AUDIO_START");
  const audioEnd = findAll(events, "BACKCHANNEL_AUDIO_END");
  const llmStart = findFirst(events, "LLM_START");
  const llmTtft = findFirst(events, "LLM_TTFT");
  const ttsStart = findFirst(events, "TTS_START");
  const ttsFirstAudio = findFirst(events, "TTS_FIRST_AUDIO");
  const agentStart = findFirst(events, "AGENT_RESPONSE_START");

  const lanes: { label: string; content: React.ReactNode }[] = [];

  // USER lane
  lanes.push({
    label: "USER",
    content: userStart && userEnd ? (
      <rect
        x={x(userStart.timestamp)}
        y={12}
        width={Math.max(2, x(userEnd.timestamp) - x(userStart.timestamp))}
        height={20}
        fill="#5b8cff"
        rx={3}
      />
    ) : null,
  });

  // STT lane
  lanes.push({
    label: "STT",
    content: (
      <>
        {stt.map((e, i) => {
          const meta = parseMeta(e.metadata_json);
          const isFinal = e.event_type === "STT_FINAL";
          const text = typeof meta.text === "string" ? meta.text : "";
          return (
            <g key={i}>
              {isFinal ? (
                <circle cx={x(e.timestamp)} cy={22} r={4} fill="#e6e9f0" />
              ) : (
                <rect x={x(e.timestamp) - 3} y={18} width={6} height={6} fill="#8b93a7" />
              )}
              <title>{`${isFinal ? "final" : "interim"}: ${text}`}</title>
            </g>
          );
        })}
      </>
    ),
  });

  // EOT lane
  lanes.push({
    label: "EOT",
    content: (
      <>
        {eotUpdates.map((e, i) => {
          const meta = parseMeta(e.metadata_json);
          const p = typeof meta.eotProbability === "number" ? meta.eotProbability : null;
          if (p === null) return null;
          return (
            <text key={i} x={x(e.timestamp)} y={26} fontSize={10} fill="#8b93a7" textAnchor="middle">
              {p.toFixed(2)}
            </text>
          );
        })}
      </>
    ),
  });

  // BACKCHANNEL lane
  lanes.push({
    label: "BACKCHANNEL",
    content: (
      <>
        {selected.map((e, i) => {
          const meta = parseMeta(e.metadata_json);
          const phrase = typeof meta.phrase === "string" ? meta.phrase : "";
          return (
            <g key={`sel-${i}`}>
              <polygon
                points={`${x(e.timestamp) - 5},32 ${x(e.timestamp) + 5},32 ${x(e.timestamp)},22`}
                fill="#5b8cff"
              />
              <title>{`selected: ${phrase}`}</title>
            </g>
          );
        })}
        {audioStart.map((e, i) => {
          const end = audioEnd[i];
          const meta = parseMeta(e.metadata_json);
          const phrase = typeof meta.phrase === "string" ? meta.phrase : "";
          const w = end ? Math.max(2, x(end.timestamp) - x(e.timestamp)) : 6;
          return (
            <g key={`play-${i}`}>
              <rect x={x(e.timestamp)} y={10} width={w} height={12} fill="#34d399" rx={2} />
              <text x={x(e.timestamp)} y={8} fontSize={9} fill="#34d399">{phrase}</text>
            </g>
          );
        })}
        {cancelled.map((e, i) => (
          <g key={`cancel-${i}`}>
            <line x1={x(e.timestamp)} y1={10} x2={x(e.timestamp)} y2={34} stroke="#f87171" strokeWidth={2} />
            <title>cancelled</title>
          </g>
        ))}
        {suppressed.map((e, i) => (
          <g key={`sup-${i}`}>
            <circle cx={x(e.timestamp)} cy={40} r={2} fill="#8b93a7" />
            <title>{`suppressed: ${JSON.stringify(parseMeta(e.metadata_json).reason ?? [])}`}</title>
          </g>
        ))}
      </>
    ),
  });

  // AGENT lane
  lanes.push({
    label: "AGENT",
    content: (
      <>
        {llmStart && llmTtft && (
          <g>
            <rect x={x(llmStart.timestamp)} y={12} width={Math.max(2, x(llmTtft.timestamp) - x(llmStart.timestamp))} height={20} fill="#232838" rx={3} />
            <title>LLM thinking (real Bedrock call)</title>
          </g>
        )}
        {ttsStart && ttsFirstAudio && (
          <g>
            <rect x={x(ttsStart.timestamp)} y={12} width={Math.max(2, x(ttsFirstAudio.timestamp) - x(ttsStart.timestamp))} height={20} fill="#3a4155" rx={3} />
            <title>TTS synthesizing (real Polly call)</title>
          </g>
        )}
        {agentStart && (
          <g>
            <rect x={x(agentStart.timestamp)} y={12} width={90} height={20} fill="#fbbf24" rx={3} strokeDasharray="3,2" stroke="#fbbf24" fillOpacity={0.4} />
            <title>agent audio begins (duration illustrative, only the start time is measured)</title>
          </g>
        )}
      </>
    ),
  });

  const chartHeight = lanes.length * LANE_HEIGHT;

  return (
    <div>
      <svg width={LABEL_WIDTH + widthPx + PADDING} height={chartHeight + 20}>
        {lanes.map((lane, i) => (
          <g key={lane.label} transform={`translate(0, ${i * LANE_HEIGHT})`}>
            <text x={0} y={26} fontSize={11} fill="#8b93a7" fontWeight={600}>
              {lane.label}
            </text>
            <line x1={LABEL_WIDTH} y1={LANE_HEIGHT - 2} x2={LABEL_WIDTH + widthPx} y2={LANE_HEIGHT - 2} stroke="#232838" />
            {lane.content}
          </g>
        ))}
      </svg>
      <div className="small" style={{ marginTop: 8 }}>
        Green = real backchannel audio window (cached clip). Yellow dashed = real agent response start (duration illustrative).
        Dark gray bars = real measured LLM/TTS call durations. Blue triangle = backchannel decision. Red line = cancelled.
      </div>
    </div>
  );
}
