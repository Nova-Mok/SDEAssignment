import fs from "fs";
import path from "path";
import type { EventRow } from "./types";

// Live demo sessions log to JSONL (apps/agent/logs/), separate from the
// benchmark's sqlite file, because a live session is a real WebRTC room
// with a real human on the mic, not a scripted replay, see README
// "Benchmark fairness" for why those two data sources are kept apart.
// This module just reads that JSONL and reshapes it into the same
// EventRow shape the Timeline component already knows how to draw.

const LOGS_DIR = path.resolve(process.cwd(), "../agent/logs");

export interface LiveSessionSummary {
  file: string;
  roomName: string;
  timestamp: string;
  mode: string;
  eventCount: number;
  backchannelCount: number;
  turnCount: number;
  startedAt: number | null;
}

function safeLogPath(file: string): string {
  const base = path.basename(file);
  if (!base.endsWith(".jsonl")) {
    throw new Error("not a log file");
  }
  const resolved = path.join(LOGS_DIR, base);
  if (path.dirname(resolved) !== LOGS_DIR) {
    throw new Error("invalid path");
  }
  return resolved;
}

function parseFilename(file: string): { roomName: string; timestamp: string } {
  const m = file.match(/^live_(.+)_(\d{8}T\d{6}Z)\.jsonl$/);
  if (!m) return { roomName: file, timestamp: "" };
  return { roomName: m[1], timestamp: m[2] };
}

export function listLiveSessions(): LiveSessionSummary[] {
  let files: string[] = [];
  try {
    files = fs.readdirSync(LOGS_DIR).filter((f) => f.endsWith(".jsonl"));
  } catch {
    return [];
  }

  return files
    .map((file) => {
      const { roomName, timestamp } = parseFilename(file);
      const events = readEvents(file);
      const backchannelCount = events.filter((e) => e.event_type === "BACKCHANNEL_AUDIO_END").length;
      const turnCount = events.filter((e) => e.event_type === "USER_SPEECH_START").length;
      const mode = events[0]?.config ?? "unknown";
      return {
        file,
        roomName,
        timestamp,
        mode,
        eventCount: events.length,
        backchannelCount,
        turnCount,
        startedAt: events[0]?.wall_time ?? null,
      };
    })
    .sort((a, b) => (b.startedAt ?? 0) - (a.startedAt ?? 0));
}

// Older logs (written before the sanitizer fix in backchannel/events.py)
// may contain bare Infinity/-Infinity/NaN tokens, which Python's json.dumps
// emits for non-finite floats but which aren't valid JSON. Strip those to
// null before parsing so old sessions still render instead of 500ing.
function parseLenient(line: string): unknown {
  const cleaned = line.replace(/([:[,]\s*)(-?Infinity|NaN)\b/g, "$1null");
  return JSON.parse(cleaned);
}

function readEvents(file: string): (EventRow & { wall_time: number })[] {
  const full = safeLogPath(file);
  let raw: string;
  try {
    raw = fs.readFileSync(full, "utf-8");
  } catch {
    return [];
  }
  return raw
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line, i) => {
      const parsed = parseLenient(line) as Record<string, any>;
      return {
        id: i,
        run_id: parsed.run_id ?? "",
        scenario_id: parsed.scenario_id ?? "",
        config: parsed.config ?? "unknown",
        event_type: parsed.event_type,
        source: parsed.source ?? "",
        timestamp: parsed.timestamp,
        wall_time: parsed.wall_time,
        turn_id: parsed.turn_id ?? -1,
        decision_id: parsed.decision_id ?? -1,
        metadata_json: JSON.stringify(parsed.metadata ?? {}),
      };
    });
}

export function getLiveSessionEvents(file: string): EventRow[] {
  return readEvents(file);
}
