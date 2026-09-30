"""Behavioral tests for BackchannelEngine — the 15 behaviors the assignment
requires, minus #15 ("backchannel mode doesn't change baseline behaviour"),
which is a worker-level claim covered in test_worker_modes.py once the
worker exists.

Two families of test here, deliberately kept separate:

- Decision-policy tests use a `ManualClock` injected into the engine and
  never touch real wall-clock time for their assertions — fast and
  deterministic, and immune to CI/machine timing jitter.
- Async-lifecycle tests (cancellation races, shutdown, slow/failed
  providers) use the engine's real default clock and real
  `asyncio.sleep`/`asyncio.create_task` scheduling, because the thing under
  test IS real task cancellation timing — a manual clock can't stand in
  for that. These use a `REALTIME_CONFIG` with tiny (tens-of-ms)
  thresholds so the suite stays fast.

Mixing the two within one test is exactly the bug that made an earlier
draft of this file flaky: an internal task calling `self._clock()` (real
time) while the test drove setup through a manual/synthetic `now=` produces
nonsensical deltas. Don't do that.
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

from backchannel.config import BackchannelConfig
from backchannel.engine import BackchannelEngine
from backchannel.events import EventType
from backchannel.instrumentation import InMemoryRecorder
from backchannel.state_machine import EngineState

from .fakes import FakeAudioProvider, ManualClock

FAST_CONFIG = BackchannelConfig(
    MIN_SPEECH_DURATION_MS=1000.0,
    BACKCHANNEL_COOLDOWN_MS=1500.0,
    MAX_EOT_PROBABILITY=0.55,
    MIN_TIME_BETWEEN_DECISIONS_MS=100.0,
    MAX_BACKCHANNELS_PER_TURN=3,
    CANCEL_ON_EOT=True,
    SUPPRESS_NEAR_EOT_MS=300.0,
    BACKCHANNEL_AUDIBLE_GRACE_MS=100.0,
    EOT_SILENCE_WINDOW_MS=1500.0,
    EOT_LONG_TURN_MS=6000.0,
)

# Real wall-clock thresholds, small enough to keep async-lifecycle tests fast
# (tens of milliseconds of real sleep) while still being comfortably larger
# than normal Python/asyncio scheduling noise.
REALTIME_CONFIG = BackchannelConfig(
    MIN_SPEECH_DURATION_MS=20.0,
    BACKCHANNEL_COOLDOWN_MS=20.0,
    MAX_EOT_PROBABILITY=0.9,
    MIN_TIME_BETWEEN_DECISIONS_MS=10.0,
    MAX_BACKCHANNELS_PER_TURN=5,
    CANCEL_ON_EOT=True,
    SUPPRESS_NEAR_EOT_MS=20.0,
    BACKCHANNEL_AUDIBLE_GRACE_MS=10.0,
    EOT_SILENCE_WINDOW_MS=5000.0,   # deliberately large: real sleeps here must never look like a pause
    EOT_LONG_TURN_MS=5000.0,
)


def make_engine(config: BackchannelConfig, **kwargs) -> tuple[BackchannelEngine, InMemoryRecorder, FakeAudioProvider]:
    recorder = InMemoryRecorder()
    provider = kwargs.pop("provider", None) or FakeAudioProvider()
    engine = BackchannelEngine(config=config, audio_provider=provider, recorder=recorder, **kwargs)
    return engine, recorder, provider


# --------------------------------------------------------------------------
# Decision-policy tests (ManualClock, synchronous except where noted)
# --------------------------------------------------------------------------

# 1. short speech -> no backchannel
def test_short_speech_does_not_trigger_backchannel():
    clock = ManualClock(0.0)
    engine, recorder, provider = make_engine(FAST_CONFIG, clock=clock)
    engine.on_agent_state_changed("listening")
    engine.on_user_state_changed("speaking")
    clock.set(0.05)
    engine.on_transcript("hi", is_final=False)

    clock.set(0.5)  # only 500ms of speech, MIN_SPEECH_DURATION_MS=1000
    engine.tick()

    suppressed = recorder.of_type(EventType.BACKCHANNEL_SUPPRESSED)
    assert len(suppressed) == 1
    assert "speech_too_short" in suppressed[0].metadata["reason"]
    assert recorder.of_type(EventType.BACKCHANNEL_SELECTED) == []
    assert provider.play_calls == []


# 2. long speech -> backchannel
@pytest.mark.asyncio
async def test_long_speech_can_trigger_backchannel():
    clock = ManualClock(0.0)
    engine, recorder, provider = make_engine(FAST_CONFIG, clock=clock)
    engine.on_agent_state_changed("listening")
    engine.on_user_state_changed("speaking")
    for t in (0.0, 0.3, 0.6, 0.9, 1.2, 1.4):
        clock.set(t)
        engine.on_transcript("and then we went to the market", is_final=False)

    clock.set(1.5)  # 1500ms speech, last transcript update 100ms ago
    engine.tick()

    selected = recorder.of_type(EventType.BACKCHANNEL_SELECTED)
    assert len(selected) == 1
    await asyncio.sleep(0)  # let the scheduled task run its first step
    await asyncio.sleep(0)
    assert provider.play_calls == [(selected[0].metadata["phrase"], selected[0].decision_id, engine.turn_id)]
    assert engine.state == EngineState.BACKCHANNEL_PLAYING
    assert recorder.of_type(EventType.BACKCHANNEL_AUDIO_START)


# 3. cooldown prevents repeated acknowledgements
@pytest.mark.asyncio
async def test_cooldown_prevents_repeated_acknowledgements():
    clock = ManualClock(0.0)
    provider = FakeAudioProvider(play_duration_s=0.02)
    engine, recorder, _ = make_engine(FAST_CONFIG, clock=clock, provider=provider)
    engine.on_agent_state_changed("listening")
    engine.on_user_state_changed("speaking")
    engine.on_transcript("talking away", is_final=False)

    clock.set(1.05)
    engine.on_transcript("talking away still", is_final=False)
    clock.set(1.1)
    engine.tick()  # triggers the first backchannel
    assert recorder.of_type(EventType.BACKCHANNEL_SELECTED)
    await asyncio.sleep(0.06)  # let the real auto-complete task finish -> _last_backchannel_at set

    clock.set(1.2)
    engine.on_transcript("still talking", is_final=False)
    clock.set(2.0)  # only ~900ms since the backchannel finished, cooldown=1500ms
    engine.tick()

    suppressed = recorder.of_type(EventType.BACKCHANNEL_SUPPRESSED)
    assert suppressed, "second attempt should have been suppressed"
    assert "cooldown_active" in suppressed[-1].metadata["reason"]
    assert len(provider.play_calls) == 1


# 4. approaching end-of-turn suppresses a candidate
def test_near_eot_silence_window_suppresses_candidate():
    clock = ManualClock(0.0)
    engine, recorder, _ = make_engine(FAST_CONFIG, clock=clock)
    engine.on_agent_state_changed("listening")
    engine.on_user_state_changed("speaking")
    engine.on_transcript("I think that's about it.", is_final=True)

    # silence_gap_ms = 1300ms; near_eot_cutoff = 1500 - 300 = 1200ms -> should suppress
    clock.set(1.3)
    engine.tick()

    suppressed = recorder.of_type(EventType.BACKCHANNEL_SUPPRESSED)
    assert suppressed
    assert "near_eot_silence_window" in suppressed[-1].metadata["reason"]


# 9. multiple STT events don't create uncontrolled async work
@pytest.mark.asyncio
async def test_transcript_events_never_schedule_async_work_directly():
    clock = ManualClock(0.0)
    engine, recorder, provider = make_engine(FAST_CONFIG, clock=clock)
    engine.on_agent_state_changed("listening")
    engine.on_user_state_changed("speaking")
    for i in range(200):
        clock.set(i * 0.001)
        engine.on_transcript(f"word {i}", is_final=False)

    await asyncio.sleep(0)
    assert engine._pending_task is None
    assert provider.play_calls == []
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert others == []


# 10. rapid speaking/silence transitions
def test_rapid_speaking_silence_transitions_do_not_raise():
    clock = ManualClock(0.0)
    engine, _, _ = make_engine(FAST_CONFIG, clock=clock)
    for i in range(50):
        engine.on_agent_state_changed("listening")
        engine.on_user_state_changed("speaking")
        clock.advance(0.01)
        engine.on_transcript(f"burst {i}", is_final=False)
        clock.advance(0.02)
        engine.on_user_state_changed("listening")
        clock.advance(0.02)
    assert engine.turn_id == 50


# 11. multiple backchannels during a long turn, capped
@pytest.mark.asyncio
async def test_multiple_backchannels_during_long_turn_respect_cap():
    clock = ManualClock(0.0)
    provider = FakeAudioProvider(play_duration_s=0.02)
    engine, recorder, _ = make_engine(FAST_CONFIG, clock=clock, provider=provider)
    engine.on_agent_state_changed("listening")
    engine.on_user_state_changed("speaking")

    t = 0.0
    for _ in range(8):
        t += 1.7  # > cooldown (1500ms) each time
        clock.set(t - 0.05)
        engine.on_transcript("keeps talking and talking", is_final=False)
        clock.set(t)
        engine.tick()
        await asyncio.sleep(0.03)  # real sleep only to let the FakeAudioProvider's real task finish

    selected = recorder.of_type(EventType.BACKCHANNEL_SELECTED)
    suppressed = recorder.of_type(EventType.BACKCHANNEL_SUPPRESSED)
    assert len(selected) == FAST_CONFIG.MAX_BACKCHANNELS_PER_TURN
    assert any("max_backchannels_per_turn_reached" in s.metadata["reason"] for s in suppressed)


# --------------------------------------------------------------------------
# Async-lifecycle tests (real clock, real sleeps, REALTIME_CONFIG)
# --------------------------------------------------------------------------

async def _speak_long_enough(engine: BackchannelEngine) -> None:
    """Get speech_duration comfortably past REALTIME_CONFIG.MIN_SPEECH_DURATION_MS
    while keeping silence_gap tiny, using only real elapsed time."""
    engine.on_agent_state_changed("listening")
    engine.on_user_state_changed("speaking")
    for _ in range(4):
        engine.on_transcript("still talking right now", is_final=False)
        await asyncio.sleep(0.015)


# 5. user stops before acknowledgement plays -> cancellation
@pytest.mark.asyncio
async def test_user_stops_before_playback_cancels_pending_backchannel():
    provider = FakeAudioProvider(synth_delay_s=0.2)  # simulate slow TTS scheduling
    engine, recorder, _ = make_engine(REALTIME_CONFIG, provider=provider)
    await _speak_long_enough(engine)

    engine.tick()
    assert recorder.of_type(EventType.BACKCHANNEL_SELECTED)

    await asyncio.sleep(0.02)  # still inside provider's synth delay
    engine.on_user_state_changed("listening")  # user stops speaking

    await asyncio.sleep(0.3)  # let the cancelled task and provider settle

    assert provider.audible_calls == [], "cancelled backchannel must never become audible"
    cancelled = recorder.of_type(EventType.BACKCHANNEL_CANCELLED)
    assert cancelled
    assert cancelled[-1].metadata["stage"] == "scheduling"


# 6. agent starts its normal response -> priority
@pytest.mark.asyncio
async def test_agent_response_starting_cancels_pending_backchannel():
    provider = FakeAudioProvider(synth_delay_s=0.2)
    engine, recorder, _ = make_engine(REALTIME_CONFIG, provider=provider)
    await _speak_long_enough(engine)

    engine.tick()
    assert recorder.of_type(EventType.BACKCHANNEL_SELECTED)

    await asyncio.sleep(0.02)
    engine.on_agent_state_changed("thinking")  # real response pipeline starting (user still speaking)

    await asyncio.sleep(0.3)
    assert provider.audible_calls == []
    assert recorder.of_type(EventType.BACKCHANNEL_CANCELLED)
    assert engine.state == EngineState.AGENT_RESPONDING


# 7. slow TTS does not block the normal response
@pytest.mark.asyncio
async def test_slow_tts_does_not_block_caller():
    provider = FakeAudioProvider(synth_delay_s=2.0)  # deliberately slow
    engine, recorder, _ = make_engine(REALTIME_CONFIG, provider=provider)
    await _speak_long_enough(engine)

    engine.tick()
    assert recorder.of_type(EventType.BACKCHANNEL_SELECTED)

    loop = asyncio.get_event_loop()
    start = loop.time()
    engine.on_agent_state_changed("thinking")  # must return immediately, not wait on the 2s "TTS"
    elapsed = loop.time() - start

    assert elapsed < 0.05, "cancelling must not block on the slow provider call"


# 8. failed TTS is handled
@pytest.mark.asyncio
async def test_failed_tts_is_handled_without_crashing():
    provider = FakeAudioProvider(fail=True)
    engine, recorder, _ = make_engine(REALTIME_CONFIG, provider=provider)
    await _speak_long_enough(engine)

    engine.tick()
    await asyncio.sleep(0.05)

    errors = recorder.of_type(EventType.ERROR)
    assert errors and errors[0].metadata["where"] == "audio_provider.play"
    # engine must still be usable afterwards
    engine.on_transcript("more talking", is_final=False)
    await asyncio.sleep(0.05)
    engine.tick()  # reaching here without an exception is the assertion
    assert True


# 12. session shutdown cancels pending work
@pytest.mark.asyncio
async def test_shutdown_cancels_pending_backchannel_and_evaluator():
    provider = FakeAudioProvider(synth_delay_s=1.0)
    engine, recorder, _ = make_engine(REALTIME_CONFIG, provider=provider)
    await _speak_long_enough(engine)

    await engine.run()
    await asyncio.sleep(0.05)  # let the evaluator tick and select mid-synth

    assert recorder.of_type(EventType.BACKCHANNEL_SELECTED)

    running = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert running, "expected the evaluator/pending tasks to be running"

    await engine.shutdown()

    assert engine.state == EngineState.SHUTDOWN
    assert recorder.of_type(EventType.SESSION_SHUTDOWN)
    remaining = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert remaining == [], "shutdown must deterministically clean up every task it started"


@pytest.mark.asyncio
async def test_shutdown_while_agent_response_is_starting():
    engine, recorder, _ = make_engine(REALTIME_CONFIG)
    engine.on_agent_state_changed("listening")
    engine.on_user_state_changed("speaking")
    engine.on_agent_state_changed("thinking")  # response starting

    await engine.shutdown()  # must not raise even mid-response
    assert engine.state == EngineState.SHUTDOWN


# 13. stale async task cannot play audio after conversation moved on
@pytest.mark.asyncio
async def test_stale_task_cannot_play_audio_after_turn_moves_on():
    provider = FakeAudioProvider(synth_delay_s=0.15)
    engine, recorder, _ = make_engine(REALTIME_CONFIG, provider=provider)
    await _speak_long_enough(engine)

    engine.tick()
    assert recorder.of_type(EventType.BACKCHANNEL_SELECTED)

    # Invalidate the turn while the provider is still "synthesizing" (mid-await).
    engine.on_user_state_changed("listening")
    engine.on_user_state_changed("speaking")  # a NEW turn starts immediately

    await asyncio.sleep(0.3)  # long enough for the old synth delay to have resolved

    assert provider.audible_calls == [], "the stale (old-turn) task must never reach audible playback"


# Cancelling an already-audible backchannel must log the cancellation exactly
# once. An earlier version double-logged it: once from `_cancel_pending`
# itself, once again from `_play_backchannel`'s own CancelledError handler
# (both fired for the same event because `_cancel_pending` cancelled the
# task unconditionally instead of deferring to that handler).
@pytest.mark.asyncio
async def test_cancelling_audible_backchannel_logs_exactly_once():
    provider = FakeAudioProvider(synth_delay_s=0.0, play_duration_s=0.5)
    engine, recorder, _ = make_engine(REALTIME_CONFIG, provider=provider)
    await _speak_long_enough(engine)

    engine.tick()
    assert recorder.of_type(EventType.BACKCHANNEL_SELECTED)

    await asyncio.sleep(0.02)  # let play() resolve; no synth delay, so it's already audible
    assert provider.audible_calls, "expected the clip to have started playing before we cancel it"
    assert engine._active_handle is not None

    engine.on_user_state_changed("listening")  # EOT while genuinely mid-playback

    await asyncio.sleep(0.1)  # let the cancelled task's own handler run

    cancelled = recorder.of_type(EventType.BACKCHANNEL_CANCELLED)
    assert len(cancelled) == 1, f"expected exactly one cancellation event, got {len(cancelled)}: {cancelled}"
    assert recorder.of_type(EventType.BACKCHANNEL_AUDIO_END) == [], "a cancelled clip must not also log a normal completion"


# The soft policy (CANCEL_ON_EOT=False) is supposed to let an already-audible
# backchannel finish naturally instead of cutting it off. Unconditionally
# cancelling the task before checking that policy made this a no-op in
# practice — the task got cancelled (and the handle stopped) regardless of
# which branch ran. This proves the soft path actually holds now.
@pytest.mark.asyncio
async def test_soft_policy_lets_audible_backchannel_finish_naturally():
    soft_config = dataclasses.replace(REALTIME_CONFIG, CANCEL_ON_EOT=False, BACKCHANNEL_AUDIBLE_GRACE_MS=5.0)
    provider = FakeAudioProvider(synth_delay_s=0.0, play_duration_s=0.15)
    engine, recorder, _ = make_engine(soft_config, provider=provider)
    await _speak_long_enough(engine)

    engine.tick()
    assert recorder.of_type(EventType.BACKCHANNEL_SELECTED)

    await asyncio.sleep(0.03)  # past the 5ms audible-grace window
    assert engine._active_handle is not None

    engine.on_user_state_changed("listening")  # EOT while audible; soft policy should NOT cancel

    await asyncio.sleep(0.2)  # long enough for the 0.15s clip to finish on its own

    assert recorder.of_type(EventType.BACKCHANNEL_CANCELLED) == [], "soft policy must not cancel an audible clip"
    assert recorder.of_type(EventType.BAD_BACKCHANNEL), "should still be flagged as audible-at-eot for measurement"
    assert recorder.of_type(EventType.BACKCHANNEL_AUDIO_END), "the clip should have been allowed to finish naturally"


# --------------------------------------------------------------------------
# 14. backchannel audio never enters the LLM/chat context
# --------------------------------------------------------------------------

def test_engine_never_references_chat_context():
    import inspect

    from backchannel import engine as engine_module

    source = inspect.getsource(engine_module)
    for forbidden in ("ChatContext", "chat_ctx", "conversation_item", "llm."):
        assert forbidden not in source, f"engine.py must never reference {forbidden!r}"

    for method_name in ("on_user_state_changed", "on_transcript", "on_agent_state_changed", "__init__"):
        sig = inspect.signature(getattr(BackchannelEngine, method_name))
        for param_name in sig.parameters:
            assert "chat" not in param_name.lower() and "llm" not in param_name.lower()
