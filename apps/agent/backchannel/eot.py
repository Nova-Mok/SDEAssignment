"""End-of-turn probability estimation.

LiveKit Agents 1.8.3 does define an ``EotPredictionEvent`` internally (with a
real ML-backed probability from the ``livekit-plugins-turn-detector``
model), but it is not wired to the public ``AgentSession`` event bus in this
release — the source routes it straight to a private ``_session_host`` and
has a literal ``# TODO: replace this direct call with the public
eot_prediction event`` comment. It's unreleased, so we don't hook it (see
README "Race conditions & private APIs").

Instead we estimate end-of-turn probability ourselves from public signals
only: how long since the transcript last updated (a real pause vs. mid-word
buffering), whether the latest transcript trails off with sentence-final
punctuation vs. a continuation word, and how long the turn has run overall.
This is intentionally simple and dependency-free — deterministic and fast
enough to run on every tick with zero network calls, which matters for the
benchmark's determinism story.

``EndOfTurnEstimator`` is a Protocol specifically so a real semantic model
(e.g. wrapping ``livekit-plugins-turn-detector``'s public
``EOUModelBase.predict_end_of_turn(chat_ctx, timeout=...)``) can be dropped
in later without touching the engine — see README "Production
considerations".
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EndOfTurnEstimator(Protocol):
    def estimate(self, *, speech_duration_ms: float, silence_gap_ms: float, last_transcript: str) -> float:
        """Return an end-of-turn probability in [0, 1]."""
        ...


class HeuristicEndOfTurnEstimator:
    _SENTENCE_FINAL = (".", "!", "?")
    _CONTINUATION_HINTS = (",", "and", "but", "so", "because", "um", "uh", "like", "-")

    def __init__(self, silence_window_ms: float = 1500.0, long_turn_ms: float = 6000.0):
        self.silence_window_ms = silence_window_ms
        self.long_turn_ms = long_turn_ms

    def estimate(self, *, speech_duration_ms: float, silence_gap_ms: float, last_transcript: str) -> float:
        silence_component = _clamp01(silence_gap_ms / self.silence_window_ms)

        text = last_transcript.strip()
        if text.endswith(self._SENTENCE_FINAL):
            punctuation_component = 1.0
        elif text == "":
            punctuation_component = 0.3
        elif any(text.lower().endswith(hint) for hint in self._CONTINUATION_HINTS):
            punctuation_component = 0.1
        else:
            punctuation_component = 0.4

        duration_component = _clamp01(speech_duration_ms / self.long_turn_ms)

        probability = 0.6 * silence_component + 0.3 * punctuation_component + 0.1 * duration_component
        return _clamp01(probability)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
