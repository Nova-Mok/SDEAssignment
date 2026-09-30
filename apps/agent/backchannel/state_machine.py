"""Explicit state model for one user turn's backchannel lifecycle.

This exists so "can a backchannel legally start playing right now" is a
lookup in a transition table, not a scatter of booleans across the engine.
Invalid transitions raise immediately rather than being silently ignored —
a bug that tries to e.g. start playback from SHUTDOWN should crash a test,
not quietly do nothing.
"""
from __future__ import annotations

from enum import Enum


class EngineState(str, Enum):
    IDLE = "IDLE"
    USER_SPEAKING = "USER_SPEAKING"
    BACKCHANNEL_CANDIDATE = "BACKCHANNEL_CANDIDATE"
    BACKCHANNEL_PENDING = "BACKCHANNEL_PENDING"
    BACKCHANNEL_PLAYING = "BACKCHANNEL_PLAYING"
    EOT_DETECTED = "EOT_DETECTED"
    AGENT_RESPONDING = "AGENT_RESPONDING"
    CANCELLED = "CANCELLED"
    SHUTDOWN = "SHUTDOWN"


class InvalidTransition(RuntimeError):
    def __init__(self, current: EngineState, target: EngineState):
        super().__init__(f"invalid transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


# SHUTDOWN is reachable from every state (a session can end at any point) and is
# added to every entry below rather than repeated by hand.
_BASE_TRANSITIONS: dict[EngineState, set[EngineState]] = {
    EngineState.IDLE: {EngineState.USER_SPEAKING},
    EngineState.USER_SPEAKING: {
        EngineState.BACKCHANNEL_CANDIDATE,
        EngineState.EOT_DETECTED,
    },
    EngineState.BACKCHANNEL_CANDIDATE: {
        EngineState.BACKCHANNEL_PENDING,   # decision: SELECT
        EngineState.USER_SPEAKING,         # decision: SUPPRESS, keep listening
        EngineState.EOT_DETECTED,          # EOT raced the decision itself
    },
    EngineState.BACKCHANNEL_PENDING: {
        EngineState.BACKCHANNEL_PLAYING,   # provider confirmed playback started
        EngineState.CANCELLED,             # invalidated before it became audible
        EngineState.EOT_DETECTED,
    },
    EngineState.BACKCHANNEL_PLAYING: {
        EngineState.USER_SPEAKING,         # clip finished naturally, turn continues
        EngineState.CANCELLED,             # stopped early per CANCEL_ON_EOT policy
        EngineState.EOT_DETECTED,
    },
    EngineState.CANCELLED: {
        EngineState.USER_SPEAKING,
        EngineState.EOT_DETECTED,
    },
    EngineState.EOT_DETECTED: {
        EngineState.AGENT_RESPONDING,
        # A "false EOT": the user resumes speaking before the agent state ever
        # confirmed a real response started (mirrors LiveKit's own
        # AgentFalseInterruptionEvent concern one layer up).
        EngineState.USER_SPEAKING,
    },
    EngineState.AGENT_RESPONDING: {
        EngineState.IDLE,
    },
    EngineState.SHUTDOWN: set(),
}

TRANSITIONS: dict[EngineState, set[EngineState]] = {
    state: targets | {EngineState.SHUTDOWN} for state, targets in _BASE_TRANSITIONS.items()
}
TRANSITIONS[EngineState.SHUTDOWN] = set()


class StateMachine:
    def __init__(self, initial: EngineState = EngineState.IDLE):
        self._state = initial
        self.history: list[EngineState] = [initial]

    @property
    def state(self) -> EngineState:
        return self._state

    def can_transition(self, target: EngineState) -> bool:
        return target in TRANSITIONS.get(self._state, set())

    def transition(self, target: EngineState) -> EngineState:
        if not self.can_transition(target):
            raise InvalidTransition(self._state, target)
        self._state = target
        self.history.append(target)
        return self._state
