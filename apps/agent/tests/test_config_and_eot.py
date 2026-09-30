from __future__ import annotations

import json

from backchannel.config import BackchannelConfig
from backchannel.eot import HeuristicEndOfTurnEstimator


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("BACKCHANNEL_MIN_SPEECH_DURATION_MS", "999")
    monkeypatch.setenv("BACKCHANNEL_CANCEL_ON_EOT", "false")
    cfg = BackchannelConfig.load()
    assert cfg.MIN_SPEECH_DURATION_MS == 999.0
    assert cfg.CANCEL_ON_EOT is False
    assert cfg.MAX_BACKCHANNELS_PER_TURN == BackchannelConfig().MAX_BACKCHANNELS_PER_TURN  # untouched default


def test_config_json_override(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"MAX_BACKCHANNELS_PER_TURN": 1}))
    cfg = BackchannelConfig.load(json_path=p)
    assert cfg.MAX_BACKCHANNELS_PER_TURN == 1


def test_config_env_wins_over_json(tmp_path, monkeypatch):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"MAX_BACKCHANNELS_PER_TURN": 1}))
    monkeypatch.setenv("BACKCHANNEL_MAX_BACKCHANNELS_PER_TURN", "5")
    cfg = BackchannelConfig.load(json_path=p)
    assert cfg.MAX_BACKCHANNELS_PER_TURN == 5


def test_eot_estimator_rises_with_silence():
    est = HeuristicEndOfTurnEstimator(silence_window_ms=1000, long_turn_ms=6000)
    low = est.estimate(speech_duration_ms=2000, silence_gap_ms=50, last_transcript="and then")
    high = est.estimate(speech_duration_ms=2000, silence_gap_ms=950, last_transcript="and then")
    assert high > low


def test_eot_estimator_sentence_final_punctuation_raises_probability():
    est = HeuristicEndOfTurnEstimator(silence_window_ms=1000, long_turn_ms=6000)
    trailing_and = est.estimate(speech_duration_ms=2000, silence_gap_ms=500, last_transcript="so I went there and")
    trailing_period = est.estimate(speech_duration_ms=2000, silence_gap_ms=500, last_transcript="so I went there.")
    assert trailing_period > trailing_and


def test_eot_estimator_bounded_0_1():
    est = HeuristicEndOfTurnEstimator(silence_window_ms=100, long_turn_ms=100)
    p = est.estimate(speech_duration_ms=100_000, silence_gap_ms=100_000, last_transcript="done.")
    assert 0.0 <= p <= 1.0
