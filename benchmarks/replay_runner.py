"""Runs one deterministic scenario script against a real STT-less pipeline:

WHAT'S SCRIPTED (deterministic, repeatable, no live mic):
  - user speech timing and transcript content (scenarios.py)

WHAT'S REAL (measured, not fabricated):
  - the LLM call (AWS Bedrock, Amazon Nova Micro) that produces the agent's
    reply, including its real time-to-first-token
  - the TTS call (Amazon Polly) that synthesizes that reply, including its
    real time-to-first-audio-chunk
  - for backchannel runs, the BackchannelEngine's real decision timing and
    the CachedAudioProvider's real (near-zero) scheduling overhead

This intentionally runs with NO live LiveKit room: routing through a real
WebRTC room would add network/media jitter as a confound between the two
configs being compared, which is exactly the "is a 30ms difference my
system or normal variance" trap the assignment calls out. See README
"Benchmark fairness".

response_latency_ms = TTS_FIRST_AUDIO - USER_SPEECH_END is the headline
number the assignment calls "the most important result".
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent / "apps" / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from livekit.agents.llm import ChatContext  # noqa: E402

from aws_providers import make_llm, make_tts  # noqa: E402
from backchannel.audio_provider import CachedAudioProvider  # noqa: E402
from backchannel.config import BackchannelConfig  # noqa: E402
from backchannel.engine import BackchannelEngine  # noqa: E402
from backchannel.events import Event, EventType  # noqa: E402
from backchannel.instrumentation import SqliteRecorder  # noqa: E402

from scenarios import Scenario  # noqa: E402

CLIP_DIR = AGENT_DIR / "cached_clips"


def _load_clip_durations() -> dict[str, float]:
    import json

    manifest = CLIP_DIR / "durations.json"
    if manifest.exists():
        return json.loads(manifest.read_text())
    return {}


CLIP_DURATIONS = _load_clip_durations()


class _Harness:
    """Thin recorder-facing wrapper so run_one() doesn't repeat run_id/scenario_id/config everywhere."""

    def __init__(self, recorder: SqliteRecorder, run_id: str, scenario_id: str, config_label: str):
        self._recorder = recorder
        self._run_id = run_id
        self._scenario_id = scenario_id
        self._config_label = config_label

    def record(self, event_type: EventType, source: str = "harness", **metadata) -> float:
        ts = time.monotonic()
        self._recorder.record(
            Event(
                event_type=event_type,
                timestamp=ts,
                run_id=self._run_id,
                scenario_id=self._scenario_id,
                config=self._config_label,
                source=source,
                metadata=metadata,
            )
        )
        return ts


async def run_one(
    scenario: Scenario,
    config_label: str,  # "baseline" | "backchannel"
    config: BackchannelConfig,
    recorder: SqliteRecorder,
    run_index: int,
    seed: int,
    llm=None,
    tts=None,
) -> dict:
    """`llm`/`tts` should be shared, warmed-up client instances reused across
    runs (matching how a real long-lived AgentSession reuses its clients
    across turns) — constructing a fresh one per call folds one-time
    connection setup into what's supposed to be a steady-state TTFT
    measurement. Falls back to constructing one-off clients if omitted."""
    run_id = f"{scenario.id}__{config_label}__{run_index}__{uuid.uuid4().hex[:8]}"
    h = _Harness(recorder, run_id, scenario.id, config_label)

    engine: BackchannelEngine | None = None
    if config_label == "backchannel":
        import random

        provider = CachedAudioProvider(clip_dir=CLIP_DIR, clip_duration_ms=CLIP_DURATIONS)
        engine = BackchannelEngine(
            config=config,
            audio_provider=provider,
            recorder=recorder,
            rng=random.Random(seed),
            run_id=run_id,
            scenario_id=scenario.id,
            config_label=config_label,
        )
        await engine.run()

    h.record(EventType.USER_SPEECH_START, source="harness")
    if engine:
        engine.on_agent_state_changed("listening")
        engine.on_user_state_changed("speaking")

    last_offset = 0.0
    for ev in scenario.events:
        await asyncio.sleep(max(0.0, (ev.offset_ms - last_offset) / 1000))
        last_offset = ev.offset_ms
        event_type = EventType.STT_FINAL if ev.kind == "final" else EventType.STT_INTERIM
        h.record(event_type, source="scripted_stt", text=ev.text, is_final=(ev.kind == "final"))
        if engine:
            engine.on_transcript(ev.text, is_final=(ev.kind == "final"))

    await asyncio.sleep(max(0.0, (scenario.turn_end_offset_ms - last_offset) / 1000))
    user_speech_end_ts = h.record(EventType.USER_SPEECH_END, source="harness")
    if engine:
        engine.on_user_state_changed("listening")
        engine.on_agent_state_changed("thinking")  # real response pipeline starting now

    # --- Real LLM call ---------------------------------------------------
    llm_start_ts = h.record(EventType.LLM_START, source="aws_llm")
    llm = llm or make_llm()
    chat_ctx = ChatContext()
    chat_ctx.add_message(role="user", content=scenario.final_transcript)
    llm_stream = llm.chat(chat_ctx=chat_ctx)
    llm_ttft_ts: float | None = None
    response_chunks: list[str] = []
    async for chunk in llm_stream:
        content = chunk.delta.content if chunk.delta else None
        if content:
            if llm_ttft_ts is None:
                llm_ttft_ts = h.record(EventType.LLM_TTFT, source="aws_llm")
            response_chunks.append(content)
    await llm_stream.aclose()
    response_text = "".join(response_chunks).strip() or "Got it, thanks."
    if llm_ttft_ts is None:
        llm_ttft_ts = time.monotonic()

    # Amazon Polly's SynthesizeSpeech caps input at 3000 chars; a small fast
    # model occasionally rambles well past a normal conversational reply.
    # Truncate defensively at a sentence boundary rather than let a single
    # long completion take down the whole benchmark run (this is exactly the
    # "TTS failure" risk the assignment asks about — real, not contrived).
    MAX_TTS_CHARS = 800
    if len(response_text) > MAX_TTS_CHARS:
        truncated = response_text[:MAX_TTS_CHARS]
        last_period = truncated.rfind(". ")
        response_text = truncated[: last_period + 1] if last_period > 100 else truncated

    # --- Real TTS call -----------------------------------------------------
    tts_start_ts = h.record(EventType.TTS_START, source="aws_tts")
    tts = tts or make_tts()
    tts_first_audio_ts: float | None = None
    try:
        tts_stream = tts.synthesize(response_text)
        async for _audio in tts_stream:
            if tts_first_audio_ts is None:
                tts_first_audio_ts = h.record(EventType.TTS_FIRST_AUDIO, source="aws_tts")
                break
        await tts_stream.aclose()
    except Exception as exc:  # noqa: BLE001 - a TTS failure must not kill the whole benchmark matrix
        h.record(EventType.ERROR, source="aws_tts", where="tts.synthesize", error=repr(exc))
    if tts_first_audio_ts is None:
        tts_first_audio_ts = time.monotonic()

    h.record(EventType.AGENT_RESPONSE_START, source="harness")
    if engine:
        engine.on_agent_state_changed("speaking")
        await asyncio.sleep(0.05)  # let any in-flight cancellation settle before shutdown
        await engine.shutdown()
        engine.on_agent_state_changed("idle")

    response_latency_ms = (tts_first_audio_ts - user_speech_end_ts) * 1000
    llm_ttft_ms = (llm_ttft_ts - llm_start_ts) * 1000
    tts_first_audio_ms = (tts_first_audio_ts - tts_start_ts) * 1000
    stt_finalization_ms = scenario.turn_end_offset_ms - (
        max((e.offset_ms for e in scenario.events if e.kind == "final"), default=0.0)
    )  # scripted, not measured

    behaviour = _summarize_behaviour(recorder, run_id, user_speech_end_ts)

    run_row = {
        "run_id": run_id,
        "scenario_id": scenario.id,
        "config": config_label,
        "run_index": run_index,
        "started_at": llm_start_ts,
        "response_latency_ms": response_latency_ms,
        "backchannel_latency_ms": behaviour["avg_backchannel_latency_ms"],
        "llm_ttft_ms": llm_ttft_ms,
        "tts_first_audio_ms": tts_first_audio_ms,
        "stt_finalization_ms": stt_finalization_ms,
        "backchannel_count": behaviour["backchannel_count"],
        "cancelled_count": behaviour["cancelled_count"],
        "bad_backchannel_count": behaviour["bad_backchannel_count"],
        "suppressed_count": behaviour["suppressed_count"],
        "overlap_ms": behaviour["overlap_ms"],
        "notes_json": "{}",
    }
    recorder.upsert_run(run_row)
    return run_row


def _summarize_behaviour(recorder: SqliteRecorder, run_id: str, user_speech_end_ts: float) -> dict:
    rows = recorder.events_for_run(run_id)

    backchannel_count = sum(1 for r in rows if r[0] == EventType.BACKCHANNEL_AUDIO_END.value)
    cancelled_count = sum(1 for r in rows if r[0] == EventType.BACKCHANNEL_CANCELLED.value)
    bad_backchannel_count = sum(1 for r in rows if r[0] == EventType.BAD_BACKCHANNEL.value)
    suppressed_count = sum(1 for r in rows if r[0] == EventType.BACKCHANNEL_SUPPRESSED.value)

    selected_ts = [r[1] for r in rows if r[0] == EventType.BACKCHANNEL_SELECTED.value]
    audio_start_ts = [r[1] for r in rows if r[0] == EventType.BACKCHANNEL_AUDIO_START.value]
    audio_end_ts = [r[1] for r in rows if r[0] == EventType.BACKCHANNEL_AUDIO_END.value]

    latencies = []
    for sel, start in zip(selected_ts, audio_start_ts):
        latencies.append((start - sel) * 1000)
    avg_backchannel_latency_ms = sum(latencies) / len(latencies) if latencies else None

    overlap_ms = 0.0
    for end in audio_end_ts:
        if end > user_speech_end_ts:
            overlap_ms += (end - user_speech_end_ts) * 1000

    return {
        "backchannel_count": backchannel_count,
        "cancelled_count": cancelled_count,
        "bad_backchannel_count": bad_backchannel_count,
        "suppressed_count": suppressed_count,
        "avg_backchannel_latency_ms": avg_backchannel_latency_ms,
        "overlap_ms": overlap_ms,
    }
