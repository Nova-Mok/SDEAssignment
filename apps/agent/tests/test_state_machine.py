"""The state machine is a correctness-critical, separately reviewable unit:
these tests pin down which transitions are legal without going through the
engine at all."""
from __future__ import annotations

import pytest

from backchannel.state_machine import EngineState, InvalidTransition, StateMachine


def test_happy_path_full_turn():
    sm = StateMachine()
    assert sm.state == EngineState.IDLE
    sm.transition(EngineState.USER_SPEAKING)
    sm.transition(EngineState.BACKCHANNEL_CANDIDATE)
    sm.transition(EngineState.BACKCHANNEL_PENDING)
    sm.transition(EngineState.BACKCHANNEL_PLAYING)
    sm.transition(EngineState.USER_SPEAKING)  # clip finished, turn continues
    sm.transition(EngineState.EOT_DETECTED)
    sm.transition(EngineState.AGENT_RESPONDING)
    sm.transition(EngineState.IDLE)
    assert sm.history[0] == EngineState.IDLE and sm.history[-1] == EngineState.IDLE


def test_suppressed_candidate_returns_to_speaking():
    sm = StateMachine()
    sm.transition(EngineState.USER_SPEAKING)
    sm.transition(EngineState.BACKCHANNEL_CANDIDATE)
    sm.transition(EngineState.USER_SPEAKING)  # suppressed
    assert sm.state == EngineState.USER_SPEAKING


def test_cancellation_paths():
    sm = StateMachine()
    sm.transition(EngineState.USER_SPEAKING)
    sm.transition(EngineState.BACKCHANNEL_CANDIDATE)
    sm.transition(EngineState.BACKCHANNEL_PENDING)
    sm.transition(EngineState.CANCELLED)  # invalidated before it became audible
    sm.transition(EngineState.USER_SPEAKING)


def test_false_eot_recovery():
    sm = StateMachine()
    sm.transition(EngineState.USER_SPEAKING)
    sm.transition(EngineState.EOT_DETECTED)
    sm.transition(EngineState.USER_SPEAKING)  # user resumed before agent actually responded


def test_shutdown_reachable_from_every_state():
    for state in EngineState:
        if state == EngineState.SHUTDOWN:
            continue
        sm = StateMachine(initial=state)
        sm.transition(EngineState.SHUTDOWN)
        assert sm.state == EngineState.SHUTDOWN


def test_invalid_transition_raises():
    sm = StateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition(EngineState.AGENT_RESPONDING)  # can't jump straight from IDLE


def test_shutdown_is_terminal():
    sm = StateMachine(initial=EngineState.SHUTDOWN)
    with pytest.raises(InvalidTransition):
        sm.transition(EngineState.IDLE)
