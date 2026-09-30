"""Test doubles — no real network/audio anywhere in the unit test suite."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from backchannel.audio_provider import BackchannelAudioProvider, PlaybackHandle


class ManualClock:
    """A controllable clock for deterministic, fast decision-policy tests.

    Every timestamp the engine records (turn start, last transcript, last
    backchannel, handle.started_at) goes through the SAME clock instance
    injected into the engine, so advancing it is the only way time moves —
    no hidden dependency on real wall-clock time sneaks in.
    """

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def set(self, value: float) -> None:
        self.now = value

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeAudioProvider(BackchannelAudioProvider):
    """Controllable delay/failure, so tests can force exactly the races the
    assignment asks about (slow TTS, failed TTS, cancel-before-audible)."""

    def __init__(
        self,
        phrases: list[str] | None = None,
        synth_delay_s: float = 0.0,
        play_duration_s: float = 0.2,
        fail: bool = False,
        on_audible: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._phrases = phrases or ["mm-hmm", "okay", "right", "yeah", "got-it"]
        self.synth_delay_s = synth_delay_s
        self.play_duration_s = play_duration_s
        self.fail = fail
        self.on_audible = on_audible
        self.clock = clock
        self.play_calls: list[tuple[str, int, int]] = []
        self.audible_calls: list[str] = []
        self._tasks: set[asyncio.Task] = set()

    def available_phrases(self) -> list[str]:
        return list(self._phrases)

    async def play(self, phrase: str, *, decision_id: int, turn_id: int) -> PlaybackHandle:
        self.play_calls.append((phrase, decision_id, turn_id))
        if self.synth_delay_s:
            await asyncio.sleep(self.synth_delay_s)
        if self.fail:
            raise RuntimeError("simulated provider failure")

        # Only reached if this coroutine was NOT cancelled during the delay above —
        # this is exactly the "stale task cannot play audio" guarantee under test.
        handle = PlaybackHandle(decision_id=decision_id, turn_id=turn_id, started_at=self.clock())
        self.audible_calls.append(phrase)
        if self.on_audible is not None:
            self.on_audible(phrase)
        task = asyncio.create_task(self._auto_complete(handle))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return handle

    async def _auto_complete(self, handle: PlaybackHandle) -> None:
        try:
            await asyncio.wait_for(handle.wait(), timeout=self.play_duration_s)
        except TimeoutError:
            handle.mark_done()
