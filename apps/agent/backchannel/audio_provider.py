"""Backchannel audio delivery, abstracted behind ``BackchannelAudioProvider``.

Two implementations, matching the assignment's explicit tradeoff question
("would pre-generated audio be better?"):

- ``CachedAudioProvider`` plays a pre-rendered clip. The only latency before
  "audible" is a file read handoff — near-zero and deterministic. This is
  what the benchmark uses by default, because it removes TTS network
  variance as a confound when comparing baseline vs. backchannel latency.
- ``TTSAudioProvider`` synthesizes the phrase on demand via a real TTS call
  (injected as ``synthesize``, e.g. a real Polly call in production). More
  flexible (new phrases, other languages) but the synthesis latency sits
  directly in front of "audible" — exactly the "what if generating mm-hmm
  takes 500ms" risk the assignment calls out. See README "Audio strategy".

Both return a ``PlaybackHandle`` immediately-ish; the engine never blocks
on playback completion, only on scheduling.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PlaybackHandle:
    decision_id: int
    turn_id: int
    started_at: float
    _done_event: asyncio.Event = field(default_factory=asyncio.Event)

    def stop(self) -> None:
        """Idempotent: stopping an already-done handle is a no-op."""
        self._done_event.set()

    def mark_done(self) -> None:
        self._done_event.set()

    def done(self) -> bool:
        return self._done_event.is_set()

    async def wait(self) -> None:
        await self._done_event.wait()


class BackchannelAudioProvider(ABC):
    @abstractmethod
    async def play(self, phrase: str, *, decision_id: int, turn_id: int) -> PlaybackHandle:
        """Begin playback of ``phrase``. Returns a handle; may itself take
        real time (e.g. a TTS network call) before returning."""

    @abstractmethod
    def available_phrases(self) -> list[str]:
        ...


class CachedAudioProvider(BackchannelAudioProvider):
    def __init__(
        self,
        clip_dir: Path,
        clip_duration_ms: dict[str, float] | None = None,
        on_play: Callable[[str, Path], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._clip_dir = Path(clip_dir)
        self._clip_duration_ms = dict(clip_duration_ms or {})
        discovered = sorted(p.stem for p in self._clip_dir.glob("*.wav")) if self._clip_dir.exists() else []
        self._phrases = discovered or list(self._clip_duration_ms.keys())
        self._on_play = on_play
        self._clock = clock
        self._tasks: set[asyncio.Task] = set()

    def available_phrases(self) -> list[str]:
        return list(self._phrases)

    async def play(self, phrase: str, *, decision_id: int, turn_id: int) -> PlaybackHandle:
        handle = PlaybackHandle(decision_id=decision_id, turn_id=turn_id, started_at=self._clock())
        if self._on_play is not None:
            self._on_play(phrase, self._clip_dir / f"{phrase}.wav")
        duration_s = self._clip_duration_ms.get(phrase, 500.0) / 1000.0
        task = asyncio.create_task(self._auto_complete(handle, duration_s))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return handle

    async def _auto_complete(self, handle: PlaybackHandle, duration_s: float) -> None:
        try:
            await asyncio.wait_for(handle.wait(), timeout=duration_s)
        except TimeoutError:
            handle.mark_done()


SynthesizeFn = Callable[[str], Awaitable[tuple[bytes, float]]]  # phrase -> (audio_bytes, duration_ms)


class TTSAudioProvider(BackchannelAudioProvider):
    def __init__(
        self,
        phrases: list[str],
        synthesize: SynthesizeFn,
        on_play: Callable[[str, bytes], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._phrases = list(phrases)
        self._synthesize = synthesize
        self._on_play = on_play
        self._clock = clock
        self._tasks: set[asyncio.Task] = set()

    def available_phrases(self) -> list[str]:
        return list(self._phrases)

    async def play(self, phrase: str, *, decision_id: int, turn_id: int) -> PlaybackHandle:
        handle = PlaybackHandle(decision_id=decision_id, turn_id=turn_id, started_at=self._clock())
        # The real network/synthesis latency happens here, BEFORE anything is audible.
        audio_bytes, duration_ms = await self._synthesize(phrase)
        if handle.done():
            # Cancelled while we were synthesizing (e.g. task.cancel() raced this await,
            # or something externally marked the handle done) — never touch playback.
            return handle
        if self._on_play is not None:
            self._on_play(phrase, audio_bytes)
        task = asyncio.create_task(self._auto_complete(handle, duration_ms / 1000.0))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return handle

    async def _auto_complete(self, handle: PlaybackHandle, duration_s: float) -> None:
        try:
            await asyncio.wait_for(handle.wait(), timeout=duration_s)
        except TimeoutError:
            handle.mark_done()
