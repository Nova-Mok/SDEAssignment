"""Shared event schema.

One schema powers instrumentation, the benchmark's stored results, and the
web UI's timeline — there is deliberately no second, inconsistent logging
path anywhere else in this codebase.
"""
from __future__ import annotations

import dataclasses
import math
import time
from enum import Enum
from typing import Any


def _json_safe(value: Any) -> Any:
    """Recursively replace non-finite floats (inf/-inf/nan) with None.

    `json.dumps` happily emits the bare tokens `Infinity`/`-Infinity`/`NaN`
    for these (a Python-ism, not valid JSON per spec), which a strict
    `JSON.parse` on the reading side then rejects outright. `tick()`'s
    `timeSinceLastBackchannelMs` is `float("inf")` before the first
    backchannel of a session, so this is a real, reachable case, not a
    hypothetical one.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


class EventType(str, Enum):
    USER_SPEECH_START = "USER_SPEECH_START"
    USER_SPEECH_END = "USER_SPEECH_END"
    STT_INTERIM = "STT_INTERIM"
    STT_FINAL = "STT_FINAL"
    EOT_PROBABILITY_UPDATED = "EOT_PROBABILITY_UPDATED"
    EOT_DETECTED = "EOT_DETECTED"

    BACKCHANNEL_CANDIDATE = "BACKCHANNEL_CANDIDATE"
    BACKCHANNEL_SELECTED = "BACKCHANNEL_SELECTED"
    BACKCHANNEL_SUPPRESSED = "BACKCHANNEL_SUPPRESSED"
    BACKCHANNEL_CANCELLED = "BACKCHANNEL_CANCELLED"
    BACKCHANNEL_AUDIO_START = "BACKCHANNEL_AUDIO_START"
    BACKCHANNEL_AUDIO_END = "BACKCHANNEL_AUDIO_END"
    BAD_BACKCHANNEL = "BAD_BACKCHANNEL"

    AGENT_STATE_CHANGED = "AGENT_STATE_CHANGED"
    LLM_START = "LLM_START"
    LLM_TTFT = "LLM_TTFT"
    TTS_START = "TTS_START"
    TTS_FIRST_AUDIO = "TTS_FIRST_AUDIO"
    AGENT_RESPONSE_START = "AGENT_RESPONSE_START"
    AGENT_RESPONSE_END = "AGENT_RESPONSE_END"

    SESSION_SHUTDOWN = "SESSION_SHUTDOWN"
    ERROR = "ERROR"


@dataclasses.dataclass
class Event:
    event_type: EventType
    timestamp: float                       # time.monotonic() by default — see clock note below
    run_id: str = ""
    scenario_id: str = ""
    config: str = ""                        # "baseline" | "backchannel"
    source: str = "engine"                  # "engine" | "aws_llm" | "aws_tts" | "aws_stt" | "harness"
    turn_id: int = -1
    decision_id: int = -1
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    wall_time: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "wall_time": self.wall_time,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "config": self.config,
            "source": self.source,
            "turn_id": self.turn_id,
            "decision_id": self.decision_id,
            "metadata": _json_safe(self.metadata),
        }
