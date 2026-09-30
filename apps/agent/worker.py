"""LiveKit Agents entrypoint. Baseline and backchannel modes share every
line of pipeline setup below except the `if mode == "backchannel":` block —
that symmetry is the whole point (see README "Fairness" and
test_worker_modes.py's baseline-unaffected assertion).

Mode is read from room metadata (`{"mode": "baseline" | "backchannel"}`),
set by whoever creates the room (the web app's live-demo page, or
`scripts/dispatch_room.py` for manual testing). Defaults to "baseline" if
missing or malformed — the safer default when in doubt.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.agents.voice.background_audio import BackgroundAudioPlayer

from aws_providers import make_llm, make_stt, make_tts
from backchannel.config import BackchannelConfig
from backchannel.engine import BackchannelEngine
from backchannel.instrumentation import JsonlRecorder
from livekit_adapter import LiveKitCachedAudioProvider, wire_session_events

logger = logging.getLogger("backchannel-worker")

CLIP_DIR = Path(__file__).resolve().parent / "cached_clips"
LOG_DIR = Path(__file__).resolve().parent / "logs"

INSTRUCTIONS = (
    "You are a warm, concise voice assistant having a natural spoken conversation. "
    "Keep replies short and conversational, a sentence or two at a time, because this is "
    "a live voice call, not a chat window."
)


def _read_mode(ctx: JobContext) -> str:
    raw = ctx.room.metadata or ""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    mode = data.get("mode", "baseline")
    return mode if mode in ("baseline", "backchannel") else "baseline"


def build_session() -> AgentSession:
    """The one pipeline definition both modes share, byte for byte."""
    return AgentSession(
        stt=make_stt(),
        llm=make_llm(),
        tts=make_tts(),
        turn_detection="stt",
    )


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    mode = _read_mode(ctx)
    logger.info("session starting: mode=%s room=%s", mode, ctx.room.name)

    session = build_session()
    agent = Agent(instructions=INSTRUCTIONS)

    engine: BackchannelEngine | None = None
    bg_audio: BackgroundAudioPlayer | None = None

    if mode == "backchannel":
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        recorder = JsonlRecorder(LOG_DIR / f"live_{ctx.room.name}_{ts}.jsonl")

        bg_audio = BackgroundAudioPlayer()
        await bg_audio.start(room=ctx.room, agent_session=session)

        provider = LiveKitCachedAudioProvider(bg_audio, CLIP_DIR)
        config = BackchannelConfig.load()
        engine = BackchannelEngine(
            config=config,
            audio_provider=provider,
            recorder=recorder,
            run_id=ctx.room.name,
            scenario_id="live",
            config_label="backchannel",
        )
        wire_session_events(session, engine)
        await engine.run()
        logger.info("backchannel engine running (config=%s)", config)

    async def _shutdown() -> None:
        if engine is not None:
            await engine.shutdown()
        if bg_audio is not None:
            await bg_audio.aclose()

    ctx.add_shutdown_callback(_shutdown)

    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
