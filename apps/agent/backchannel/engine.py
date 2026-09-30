"""BackchannelEngine: the decision policy + async lifecycle.

Design summary (see README for the full write-up):

- Framework-agnostic. Nothing here imports livekit. Inputs arrive through
  three plain methods (`on_user_state_changed`, `on_transcript`,
  `on_agent_state_changed`) that any adapter — a real AgentSession, or the
  benchmark's scripted replay — can call with plain data. This is what
  makes the 15 required behavioral tests possible without a live
  connection.

- Bounded async work. STT/state events only update an in-memory snapshot
  (no async work per event). A single periodic evaluator task calls
  `tick()` at most every `MIN_TIME_BETWEEN_DECISIONS_MS`. At most one
  backchannel (`_pending_task` / `_active_handle`) is in flight at a time.

- Cancellation via real asyncio task cancellation, not a flag some other
  coroutine has to remember to check. `_pending_task.cancel()` reaches into
  the audio provider's `play()` coroutine at whatever await point it's
  at (including mid-TTS-synthesis) and raises there — a stale attempt
  cannot go on to play audio after the conversation has moved on.

- Priority rule: a real agent response always wins. Both the EOT path
  (user_state speaking -> listening) and the agent-state path (agent_state
  -> thinking/speaking) independently trigger cancellation, so whichever
  signal arrives first wins — the assignment's "who has priority" race is
  answered by "the real response, checked from two directions."
"""
from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import Callable

from .audio_provider import BackchannelAudioProvider, PlaybackHandle
from .config import BackchannelConfig
from .eot import EndOfTurnEstimator, HeuristicEndOfTurnEstimator
from .events import Event, EventType
from .instrumentation import EventRecorder
from .state_machine import EngineState, StateMachine

UserState = str    # "speaking" | "listening" | "away"
AgentState = str   # "initializing" | "idle" | "listening" | "thinking" | "speaking"

_RESPONDING_AGENT_STATES = ("thinking", "speaking")


class BackchannelEngine:
    def __init__(
        self,
        config: BackchannelConfig,
        audio_provider: BackchannelAudioProvider,
        eot_estimator: EndOfTurnEstimator | None = None,
        recorder: EventRecorder | None = None,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        run_id: str = "",
        scenario_id: str = "",
        config_label: str = "backchannel",
    ):
        self._config = config
        self._audio_provider = audio_provider
        self._eot_estimator = eot_estimator or HeuristicEndOfTurnEstimator(
            silence_window_ms=config.EOT_SILENCE_WINDOW_MS,
            long_turn_ms=config.EOT_LONG_TURN_MS,
        )
        self._recorder = recorder
        self._clock = clock
        self._rng = rng or random.Random()
        self._run_id = run_id
        self._scenario_id = scenario_id
        self._config_label = config_label

        self._sm = StateMachine(EngineState.IDLE)

        self._user_state: UserState = "away"
        self._agent_state: AgentState = "initializing"

        self._turn_id = 0
        self._decision_id = 0
        self._turn_start_at = self._clock()
        self._last_transcript_at = self._clock()
        self._last_transcript_text = ""
        self._backchannels_this_turn = 0
        self._last_backchannel_at: float | None = None
        self._last_phrase: str | None = None

        self._pending_task: asyncio.Task | None = None
        self._active_handle: PlaybackHandle | None = None
        self._evaluator_task: asyncio.Task | None = None
        self._shut_down = False

    # ------------------------------------------------------------------ #
    # Public state
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> EngineState:
        return self._sm.state

    @property
    def turn_id(self) -> int:
        return self._turn_id

    @property
    def backchannels_this_turn(self) -> int:
        return self._backchannels_this_turn

    # ------------------------------------------------------------------ #
    # Public event-ingestion API (called by an adapter: real or scripted)
    # ------------------------------------------------------------------ #

    def on_user_state_changed(self, new_state: UserState, now: float | None = None) -> None:
        now = now if now is not None else self._clock()
        old_state = self._user_state
        self._user_state = new_state

        if new_state == "speaking" and old_state != "speaking":
            self._turn_id += 1
            self._turn_start_at = now
            self._last_transcript_at = now
            self._last_transcript_text = ""
            self._backchannels_this_turn = 0
            if self._sm.state == EngineState.AGENT_RESPONDING:
                self._sm.transition(EngineState.IDLE)
            if self._sm.state != EngineState.USER_SPEAKING:
                self._sm.transition(EngineState.USER_SPEAKING)
            self._record(EventType.USER_SPEECH_START)
        elif new_state != "speaking" and old_state == "speaking":
            self._record(EventType.USER_SPEECH_END)
            self._handle_eot(now)

    def on_transcript(self, text: str, is_final: bool, now: float | None = None) -> None:
        now = now if now is not None else self._clock()
        self._last_transcript_at = now
        self._last_transcript_text = text
        self._record(EventType.STT_FINAL if is_final else EventType.STT_INTERIM, metadata={"text": text, "is_final": is_final})

    def on_agent_state_changed(self, new_state: AgentState, now: float | None = None) -> None:
        now = now if now is not None else self._clock()
        old_state = self._agent_state
        self._agent_state = new_state
        self._record(EventType.AGENT_STATE_CHANGED, metadata={"old_state": old_state, "new_state": new_state})

        responding_now = new_state in _RESPONDING_AGENT_STATES
        responding_before = old_state in _RESPONDING_AGENT_STATES

        if responding_now and not responding_before:
            self._cancel_pending(reason="agent_responding", now=now)
            if self._sm.state in (
                EngineState.USER_SPEAKING,
                EngineState.BACKCHANNEL_CANDIDATE,
                EngineState.BACKCHANNEL_PENDING,
                EngineState.BACKCHANNEL_PLAYING,
                EngineState.CANCELLED,
            ):
                self._sm.transition(EngineState.EOT_DETECTED)
            if self._sm.state == EngineState.EOT_DETECTED:
                self._sm.transition(EngineState.AGENT_RESPONDING)
        elif not responding_now and responding_before:
            if self._sm.state == EngineState.AGENT_RESPONDING:
                self._sm.transition(EngineState.IDLE)

    # ------------------------------------------------------------------ #
    # Decision policy
    # ------------------------------------------------------------------ #

    def tick(self, now: float | None = None) -> None:
        now = now if now is not None else self._clock()
        if self._shut_down or self._user_state != "speaking":
            return
        if self._pending_task is not None or self._active_handle is not None:
            return  # only one backchannel generation/playback may be active

        speech_duration_ms = (now - self._turn_start_at) * 1000
        silence_gap_ms = (now - self._last_transcript_at) * 1000
        eot_probability = self._eot_estimator.estimate(
            speech_duration_ms=speech_duration_ms,
            silence_gap_ms=silence_gap_ms,
            last_transcript=self._last_transcript_text,
        )
        self._record(
            EventType.EOT_PROBABILITY_UPDATED,
            metadata={"eotProbability": eot_probability, "speechDurationMs": speech_duration_ms, "silenceGapMs": silence_gap_ms},
        )

        since_last_bc_ms = (now - self._last_backchannel_at) * 1000 if self._last_backchannel_at is not None else float("inf")

        reasons_suppress: list[str] = []
        if speech_duration_ms < self._config.MIN_SPEECH_DURATION_MS:
            reasons_suppress.append("speech_too_short")
        if since_last_bc_ms < self._config.BACKCHANNEL_COOLDOWN_MS:
            reasons_suppress.append("cooldown_active")
        if eot_probability >= self._config.MAX_EOT_PROBABILITY:
            reasons_suppress.append("high_eot_probability")
        near_eot_cutoff_ms = max(0.0, self._config.EOT_SILENCE_WINDOW_MS - self._config.SUPPRESS_NEAR_EOT_MS)
        if silence_gap_ms >= near_eot_cutoff_ms:
            reasons_suppress.append("near_eot_silence_window")
        if self._agent_state in _RESPONDING_AGENT_STATES:
            reasons_suppress.append("agent_responding")
        if self._backchannels_this_turn >= self._config.MAX_BACKCHANNELS_PER_TURN:
            reasons_suppress.append("max_backchannels_per_turn_reached")

        self._decision_id += 1
        decision_id = self._decision_id
        log_fields = {
            "speechDurationMs": speech_duration_ms,
            "eotProbability": eot_probability,
            "timeSinceLastBackchannelMs": since_last_bc_ms,
            "agentState": self._agent_state,
        }

        if reasons_suppress:
            self._record(EventType.BACKCHANNEL_SUPPRESSED, decision_id=decision_id, metadata={**log_fields, "reason": reasons_suppress})
            return

        reasons_allow = ["sufficient_speech_duration", "cooldown_elapsed", "low_eot_probability", "agent_idle"]
        if self._sm.state == EngineState.USER_SPEAKING:
            self._sm.transition(EngineState.BACKCHANNEL_CANDIDATE)
        self._record(EventType.BACKCHANNEL_CANDIDATE, decision_id=decision_id, metadata={**log_fields, "reason": reasons_allow})

        phrase = self._choose_phrase()
        self._sm.transition(EngineState.BACKCHANNEL_PENDING)
        self._record(EventType.BACKCHANNEL_SELECTED, decision_id=decision_id, metadata={**log_fields, "reason": reasons_allow, "phrase": phrase})

        turn_id = self._turn_id
        task = asyncio.create_task(self._play_backchannel(phrase, decision_id, turn_id))
        self._pending_task = task

    def _choose_phrase(self) -> str:
        phrases = self._audio_provider.available_phrases()
        if not phrases:
            raise RuntimeError("audio provider has no available phrases")
        candidates = [p for p in phrases if p != self._last_phrase] or phrases
        phrase = self._rng.choice(candidates)
        self._last_phrase = phrase
        return phrase

    # ------------------------------------------------------------------ #
    # Playback lifecycle (runs as its own cancellable asyncio task)
    # ------------------------------------------------------------------ #

    async def _play_backchannel(self, phrase: str, decision_id: int, turn_id: int) -> None:
        handle: PlaybackHandle | None = None
        try:
            handle = await self._audio_provider.play(phrase, decision_id=decision_id, turn_id=turn_id)

            if turn_id != self._turn_id or self._shut_down:
                # The turn moved on while play() itself was in flight (e.g. mid-TTS-synthesis).
                # play() may have already started real playback despite the handle existing —
                # stop it defensively; a handle that never started audio treats stop() as a no-op.
                handle.stop()
                self._record(EventType.BACKCHANNEL_CANCELLED, turn_id=turn_id, decision_id=decision_id, metadata={"phrase": phrase, "stage": "post_schedule_stale"})
                if self._sm.state == EngineState.BACKCHANNEL_PENDING:
                    self._sm.transition(EngineState.CANCELLED)
                self._settle_after_cancel()
                return

            self._active_handle = handle
            if self._sm.state == EngineState.BACKCHANNEL_PENDING:
                self._sm.transition(EngineState.BACKCHANNEL_PLAYING)
            self._record(EventType.BACKCHANNEL_AUDIO_START, turn_id=turn_id, decision_id=decision_id, metadata={"phrase": phrase})

            await handle.wait()

            self._record(EventType.BACKCHANNEL_AUDIO_END, turn_id=turn_id, decision_id=decision_id, metadata={"phrase": phrase})
            self._backchannels_this_turn += 1
            self._last_backchannel_at = self._clock()
            if self._sm.state == EngineState.BACKCHANNEL_PLAYING:
                self._sm.transition(EngineState.USER_SPEAKING)

        except asyncio.CancelledError:
            if handle is not None and not handle.done():
                handle.stop()
            self._record(
                EventType.BACKCHANNEL_CANCELLED,
                turn_id=turn_id,
                decision_id=decision_id,
                metadata={"phrase": phrase, "stage": "playing" if handle is not None else "scheduling"},
            )
            if self._sm.state in (EngineState.BACKCHANNEL_PENDING, EngineState.BACKCHANNEL_PLAYING):
                self._sm.transition(EngineState.CANCELLED)
            self._settle_after_cancel()
            raise
        except Exception as exc:  # noqa: BLE001 - a failed/slow provider must never crash the engine
            if handle is not None and not handle.done():
                handle.stop()
            self._record(
                EventType.ERROR,
                turn_id=turn_id,
                decision_id=decision_id,
                metadata={"phrase": phrase, "error": repr(exc), "where": "audio_provider.play"},
            )
            if self._sm.state in (EngineState.BACKCHANNEL_PENDING, EngineState.BACKCHANNEL_PLAYING):
                self._sm.transition(EngineState.CANCELLED)
            self._settle_after_cancel()
            # Swallowed deliberately: a missed acknowledgement is a quality miss, not a
            # system failure, and must never propagate to (or block) the real response pipeline.
        finally:
            if self._active_handle is handle:
                self._active_handle = None
            if self._pending_task is asyncio.current_task():
                self._pending_task = None

    def _settle_after_cancel(self) -> None:
        """A cancelled/failed backchannel must not leave the state machine
        stuck at CANCELLED: if the user is still mid-turn, go back to
        USER_SPEAKING so the next tick() can evaluate a fresh candidate
        (tick()'s unconditional transition to BACKCHANNEL_PENDING would
        otherwise raise from CANCELLED). If something else already moved
        the state on (e.g. on_agent_state_changed raced us to
        AGENT_RESPONDING), this is a no-op — never fight a newer signal.
        """
        if self._sm.state == EngineState.CANCELLED and self._user_state == "speaking":
            self._sm.transition(EngineState.USER_SPEAKING)

    # ------------------------------------------------------------------ #
    # EOT / cancellation
    # ------------------------------------------------------------------ #

    def _handle_eot(self, now: float) -> None:
        if self._sm.state not in (EngineState.EOT_DETECTED, EngineState.AGENT_RESPONDING, EngineState.SHUTDOWN):
            self._sm.transition(EngineState.EOT_DETECTED)
        self._record(EventType.EOT_DETECTED)
        self._cancel_pending(reason="eot_detected", now=now)
        if self._sm.state == EngineState.EOT_DETECTED:
            self._sm.transition(EngineState.AGENT_RESPONDING)

    def _cancel_pending(self, *, reason: str, now: float) -> None:
        """Cancel whatever backchannel attempt is in flight, if any.

        `_active_handle` is only ever set while `_pending_task` is still the
        very task awaiting that handle's playout — so cancelling the task
        once playback has started is what actually stops the audio, via
        that task's own `except asyncio.CancelledError` handler in
        `_play_backchannel` (which stops the handle and logs
        BACKCHANNEL_CANCELLED exactly once). Two real bugs this fixes by
        construction, not by adding a second check: logging the
        cancellation here too used to double-count every cancellation that
        happened after playback had started (one log from here, one from
        the task's own handler), and cancelling the task unconditionally,
        before even reading `CANCEL_ON_EOT`, meant the "let an already
        audible clip finish" soft policy never actually applied — the task
        got cancelled (and the handle stopped) regardless of which branch
        ran below it.
        """
        handle = self._active_handle

        if handle is not None and not handle.done():
            elapsed_ms = (now - handle.started_at) * 1000
            should_hard_stop = self._config.CANCEL_ON_EOT or elapsed_ms < self._config.BACKCHANNEL_AUDIBLE_GRACE_MS
            if should_hard_stop:
                if self._pending_task is not None and not self._pending_task.done():
                    self._pending_task.cancel()
                if self._sm.state == EngineState.BACKCHANNEL_PLAYING:
                    self._sm.transition(EngineState.CANCELLED)
            else:
                # Softer policy: already audible and CANCEL_ON_EOT is off — let it finish
                # naturally. Do NOT cancel the task; that would stop the handle anyway.
                self._record(
                    EventType.BAD_BACKCHANNEL,
                    metadata={"category": "audible_at_eot", "reason": reason, "elapsedMs": elapsed_ms},
                )
            return

        # Nothing audible yet (still scheduling / mid-synthesis) — always safe and
        # correct to cancel outright; there's no "let it finish" case to protect
        # before anything has actually played.
        if self._pending_task is not None and not self._pending_task.done():
            self._pending_task.cancel()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        if self._evaluator_task is not None:
            return
        self._evaluator_task = asyncio.create_task(self._evaluator_loop())

    async def _evaluator_loop(self) -> None:
        interval_s = self._config.MIN_TIME_BETWEEN_DECISIONS_MS / 1000
        while True:
            self.tick()
            await asyncio.sleep(interval_s)

    async def shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        self._record(EventType.SESSION_SHUTDOWN)

        if self._evaluator_task is not None:
            self._evaluator_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._evaluator_task
            self._evaluator_task = None

        self._cancel_pending(reason="shutdown", now=self._clock())
        if self._pending_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._pending_task

        if self._sm.state != EngineState.SHUTDOWN:
            self._sm.transition(EngineState.SHUTDOWN)

    # ------------------------------------------------------------------ #

    def _record(self, event_type: EventType, *, turn_id: int | None = None, decision_id: int | None = None, metadata: dict | None = None) -> None:
        if self._recorder is None:
            return
        self._recorder.record(
            Event(
                event_type=event_type,
                timestamp=self._clock(),
                run_id=self._run_id,
                scenario_id=self._scenario_id,
                config=self._config_label,
                source="engine",
                turn_id=turn_id if turn_id is not None else self._turn_id,
                decision_id=decision_id if decision_id is not None else self._decision_id,
                metadata=metadata or {},
            )
        )
