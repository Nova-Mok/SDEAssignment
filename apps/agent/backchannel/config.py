"""All backchannel decision thresholds live here as one dataclass.

Nothing in this package hardcodes a threshold outside this file — every
number that shapes the policy is a field below, overridable via env vars
(`BACKCHANNEL_<FIELD_NAME>`) or a JSON file, so the benchmark and the live
agent can run different policies without code changes.
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path


@dataclasses.dataclass(frozen=True)
class BackchannelConfig:
    # A turn must have been speaking at least this long before we'll ever backchannel.
    MIN_SPEECH_DURATION_MS: float = 1200.0

    # Minimum time since the last backchannel (in this turn or the previous one) before another is allowed.
    BACKCHANNEL_COOLDOWN_MS: float = 2500.0

    # Above this estimated end-of-turn probability, we assume the user is finishing and suppress.
    MAX_EOT_PROBABILITY: float = 0.55

    # How often the decision policy is (re-)evaluated. Bounds async work: one evaluation per tick, not per STT event.
    MIN_TIME_BETWEEN_DECISIONS_MS: float = 200.0

    # Hard cap on backchannels within a single user turn, regardless of how long it runs.
    MAX_BACKCHANNELS_PER_TURN: int = 3

    # If True, any non-finished backchannel is stopped the instant EOT/agent-responding fires, full stop.
    # If False, a backchannel already inside its audible grace window is allowed to finish naturally
    # (softer policy — see BACKCHANNEL_AUDIBLE_GRACE_MS); only ones that haven't started yet are killed.
    CANCEL_ON_EOT: bool = True

    # Used to derive a silence-based suppression guard independent of the raw probability threshold:
    # we suppress once silence_gap_ms enters the last SUPPRESS_NEAR_EOT_MS of the EOT silence window,
    # even if the blended probability hasn't crossed MAX_EOT_PROBABILITY yet.
    SUPPRESS_NEAR_EOT_MS: float = 400.0

    # Below this elapsed-since-play() time, a backchannel is assumed not yet audible (safe to hard-cancel
    # even when CANCEL_ON_EOT is False).
    BACKCHANNEL_AUDIBLE_GRACE_MS: float = 120.0

    # EOT heuristic tuning (see eot.py) — silence gap that alone saturates the silence component to 1.0.
    EOT_SILENCE_WINDOW_MS: float = 1500.0

    # EOT heuristic tuning — speech duration that alone saturates the duration component to 1.0.
    EOT_LONG_TURN_MS: float = 6000.0

    @staticmethod
    def _coerce(raw: str, default):
        if isinstance(default, bool):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
        return raw

    @classmethod
    def load(cls, json_path: str | os.PathLike | None = None, env_prefix: str = "BACKCHANNEL_") -> "BackchannelConfig":
        """Defaults, then JSON overrides, then env var overrides (env wins)."""
        overrides: dict = {}
        if json_path is not None and Path(json_path).exists():
            overrides.update(json.loads(Path(json_path).read_text()))
        base = dataclasses.replace(cls(), **overrides) if overrides else cls()

        env_overrides: dict = {}
        for f in dataclasses.fields(cls):
            raw = os.environ.get(env_prefix + f.name)
            if raw is not None:
                env_overrides[f.name] = cls._coerce(raw, getattr(base, f.name))
        return dataclasses.replace(base, **env_overrides) if env_overrides else base


DEFAULT_CONFIG = BackchannelConfig()
