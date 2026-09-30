"""Fast, synthetic-data tests for the regression checker — no AWS calls,
no dependence on the real benchmark data, just proving the flagging logic
itself does what the docstring in regression_check.py claims: flag only
when BOTH the absolute and percentage thresholds clear, not either alone.
"""
from __future__ import annotations

from pathlib import Path

from backchannel.instrumentation import SqliteRecorder

from regression_check import check_regressions, save_baseline


def _make_db(tmp_path: Path, name: str, response_latency_values: list[float], scenario_id="s1", config="backchannel") -> Path:
    db_path = tmp_path / name
    recorder = SqliteRecorder(db_path)
    recorder.upsert_scenario(scenario_id, "S1", "synthetic", "synthetic")
    for i, v in enumerate(response_latency_values):
        recorder.upsert_run(
            {
                "run_id": f"{scenario_id}__{config}__{i}",
                "scenario_id": scenario_id,
                "config": config,
                "run_index": i,
                "started_at": 0.0,
                "response_latency_ms": v,
                "backchannel_latency_ms": None,
                "llm_ttft_ms": 100.0,
                "tts_first_audio_ms": 100.0,
                "stt_finalization_ms": 0.0,
                "backchannel_count": 0,
                "cancelled_count": 0,
                "bad_backchannel_count": 0,
                "suppressed_count": 0,
                "overlap_ms": 0.0,
                "notes_json": "{}",
            }
        )
    recorder.close()
    return db_path


def test_no_regression_when_current_matches_baseline(tmp_path):
    baseline_db = _make_db(tmp_path, "baseline.sqlite", [1000.0] * 6)
    baseline_json = tmp_path / "baseline.json"
    save_baseline(baseline_db, baseline_json)

    current_db = _make_db(tmp_path, "current.sqlite", [1000.0] * 6)
    findings = check_regressions(current_db, baseline_json)

    assert findings == []


def test_flags_when_both_thresholds_clear(tmp_path):
    baseline_db = _make_db(tmp_path, "baseline.sqlite", [1000.0] * 6)
    baseline_json = tmp_path / "baseline.json"
    save_baseline(baseline_db, baseline_json)

    # +300ms and +30% — both well past the 75ms / 12% default floors.
    current_db = _make_db(tmp_path, "current.sqlite", [1300.0] * 6)
    findings = check_regressions(current_db, baseline_json)

    assert len(findings) == 2  # p50 and p95, identical repeated values means they're equal
    for f in findings:
        assert f.scenario_id == "s1"
        assert f.config == "backchannel"
        assert f.metric == "response_latency_ms"
        assert f.delta_ms == 300.0
        assert abs(f.delta_pct - 0.30) < 1e-6


def test_small_change_is_not_flagged(tmp_path):
    baseline_db = _make_db(tmp_path, "baseline.sqlite", [1000.0] * 6)
    baseline_json = tmp_path / "baseline.json"
    save_baseline(baseline_db, baseline_json)

    # +50ms, +5% — under both floors.
    current_db = _make_db(tmp_path, "current.sqlite", [1050.0] * 6)
    findings = check_regressions(current_db, baseline_json)

    assert findings == []


def test_large_absolute_but_small_percentage_is_not_flagged(tmp_path):
    baseline_db = _make_db(tmp_path, "baseline.sqlite", [5000.0] * 6)
    baseline_json = tmp_path / "baseline.json"
    save_baseline(baseline_db, baseline_json)

    # +100ms clears the absolute floor, but only +2% — must NOT flag, proving
    # this is an AND, not an OR, of the two thresholds.
    current_db = _make_db(tmp_path, "current.sqlite", [5100.0] * 6)
    findings = check_regressions(current_db, baseline_json)

    assert findings == []


def test_large_percentage_but_small_absolute_is_not_flagged(tmp_path):
    baseline_db = _make_db(tmp_path, "baseline.sqlite", [50.0] * 6)
    baseline_json = tmp_path / "baseline.json"
    save_baseline(baseline_db, baseline_json)

    # +50ms is +100% (clears the percentage floor easily) but the absolute
    # change is below the 75ms floor — must NOT flag either.
    current_db = _make_db(tmp_path, "current.sqlite", [100.0] * 6)
    findings = check_regressions(current_db, baseline_json)

    assert findings == []


def test_improvement_is_never_flagged(tmp_path):
    baseline_db = _make_db(tmp_path, "baseline.sqlite", [2000.0] * 6)
    baseline_json = tmp_path / "baseline.json"
    save_baseline(baseline_db, baseline_json)

    current_db = _make_db(tmp_path, "current.sqlite", [1000.0] * 6)  # much faster, not a regression
    findings = check_regressions(current_db, baseline_json)

    assert findings == []
