"use client";

import { Room, RoomEvent, Track } from "livekit-client";
import { useRef, useState } from "react";

type Status = "idle" | "connecting" | "connected" | "disconnected" | "error";

export default function LiveDemoPage() {
  const [mode, setMode] = useState<"baseline" | "backchannel">("backchannel");
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [roomName, setRoomName] = useState<string>("");
  const roomRef = useRef<Room | null>(null);
  const audioContainerRef = useRef<HTMLDivElement | null>(null);

  async function connect() {
    setStatus("connecting");
    setError(null);
    try {
      const res = await fetch("/api/live", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "failed to create room");

      const room = new Room();
      roomRef.current = room;

      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) {
          const el = track.attach() as HTMLAudioElement;
          el.autoplay = true;
          audioContainerRef.current?.appendChild(el);
        }
      });
      room.on(RoomEvent.Disconnected, () => setStatus("disconnected"));

      await room.connect(data.url, data.token);
      await room.localParticipant.setMicrophoneEnabled(true);
      setRoomName(data.roomName);
      setStatus("connected");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
    }
  }

  async function disconnect() {
    await roomRef.current?.disconnect();
    roomRef.current = null;
    setStatus("idle");
    if (audioContainerRef.current) audioContainerRef.current.innerHTML = "";
  }

  return (
    <div className="container">
      <h1>Live Demo</h1>
      <p className="subtitle">
        Talk to the real agent (real LiveKit room, real AWS STT/LLM/TTS). Toggle backchanneling and notice
        the difference, or don&apos;t. If it&apos;s working right, the real response shouldn&apos;t feel any slower either way.
      </p>

      <div className="panel">
        <div className="toggle-row" style={{ marginBottom: 16 }}>
          <label>
            <input
              type="radio"
              name="mode"
              checked={mode === "baseline"}
              onChange={() => setMode("baseline")}
              disabled={status === "connected" || status === "connecting"}
            />{" "}
            Backchannel OFF (baseline)
          </label>
          <label>
            <input
              type="radio"
              name="mode"
              checked={mode === "backchannel"}
              onChange={() => setMode("backchannel")}
              disabled={status === "connected" || status === "connecting"}
            />{" "}
            Backchannel ON
          </label>
        </div>

        {status === "connected" || status === "connecting" ? (
          <button className="btn secondary" onClick={disconnect} disabled={status === "connecting"}>
            Disconnect
          </button>
        ) : (
          <button className="btn" onClick={connect}>
            Connect &amp; enable mic
          </button>
        )}

        <div className="small" style={{ marginTop: 12 }}>
          Status: <strong>{status}</strong>
          {roomName && <> · room {roomName}</>}
          {error && <span style={{ color: "var(--bad)" }}> · {error}</span>}
        </div>
      </div>

      <div ref={audioContainerRef} style={{ display: "none" }} />

      <h2>What to expect</h2>
      <div className="panel small">
        <p>
          <strong>Backchannel ON:</strong> while you&apos;re talking (especially a longer sentence), you should
          occasionally hear a short &quot;mm-hmm&quot;/&quot;okay&quot;/&quot;right&quot; layered under your own audio, on a
          track independent of the agent&apos;s real reply.
        </p>
        <p>
          <strong>Backchannel OFF:</strong> silence until you stop talking, then the agent responds. That&apos;s the
          classic behaviour the assignment describes as feeling unnatural on long turns.
        </p>
        <p>Requires browser microphone permission. Each connect creates a fresh room and disposes it on disconnect.</p>
      </div>
    </div>
  );
}
