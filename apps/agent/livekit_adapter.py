"""The only file that bridges `backchannel/` (framework-agnostic) to real
LiveKit Agents objects. Two responsibilities:

1. `LiveKitCachedAudioProvider` — plays a cached clip via the public
   `BackgroundAudioPlayer`, which publishes its own independent audio
   track. Wrapping its `PlayHandle` behind our own `PlaybackHandle`-shaped
   interface is what lets `BackchannelEngine` stay ignorant of LiveKit
   entirely while still driving real playback here.
2. `wire_session_events` — subscribes the three `AgentSession` events we
   established are public and sufficient (`user_state_changed`,
   `user_input_transcribed`, `agent_state_changed`) to the engine's three
   plain-data input methods. No other AgentSession internals are touched.
"""
from __future__ import annotations

from pathlib import Path

from livekit.agents.voice import AgentSession
from livekit.agents.voice.background_audio import BackgroundAudioPlayer

from backchannel.audio_provider import BackchannelAudioProvider
from backchannel.engine import BackchannelEngine


class _LiveKitPlayHandle:
    """Adapts BackgroundAudioPlayer's real PlayHandle to the
    stop()/done()/wait() shape BackchannelEngine expects."""

    def __init__(self, real_handle):
        self._real = real_handle

    def stop(self) -> None:
        self._real.stop()

    def done(self) -> bool:
        return self._real.done()

    async def wait(self) -> None:
        await self._real.wait_for_playout()


class LiveKitCachedAudioProvider(BackchannelAudioProvider):
    """Plays a pre-rendered clip through the room's real background-audio
    track. This is the production path — see README "Audio strategy" for
    why cached clips are preferred over on-demand TTS here."""

    def __init__(self, bg_audio: BackgroundAudioPlayer, clip_dir: Path):
        self._bg_audio = bg_audio
        self._clip_dir = Path(clip_dir)
        self._phrases = sorted(p.stem for p in self._clip_dir.glob("*.wav"))
        if not self._phrases:
            raise RuntimeError(f"no cached clips found in {self._clip_dir} — run scripts/generate_cached_clips.py first")

    def available_phrases(self) -> list[str]:
        return list(self._phrases)

    async def play(self, phrase: str, *, decision_id: int, turn_id: int):
        real_handle = self._bg_audio.play(str(self._clip_dir / f"{phrase}.wav"))
        return _LiveKitPlayHandle(real_handle)


def wire_session_events(session: AgentSession, engine: BackchannelEngine) -> None:
    def on_user_state_changed(ev) -> None:
        engine.on_user_state_changed(ev.new_state)

    def on_user_input_transcribed(ev) -> None:
        engine.on_transcript(ev.transcript, ev.is_final)

    def on_agent_state_changed(ev) -> None:
        engine.on_agent_state_changed(ev.new_state)

    session.on("user_state_changed", on_user_state_changed)
    session.on("user_input_transcribed", on_user_input_transcribed)
    session.on("agent_state_changed", on_agent_state_changed)
