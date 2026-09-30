"""Bonus: automatic latency regression detection.

Stores a baseline snapshot of `response_latency_ms` P50/P95 per
(scenario, config) from a known-good benchmark run, then compares a later
run's sqlite data against it. A regression is flagged only when a change
clears BOTH an absolute floor and a relative floor (default 75ms AND 12%),
on purpose: the Results section already shows P95 alone swinging by
hundreds of ms between two runs of identical code from pure AWS variance
(see README "What became slower with backchanneling?"), so a checker that
fires on any small delta would be pure noise. Requiring both bars raises
the threshold high enough to be believable while staying, deliberately,
far short of a real statistical hypothesis test — six samples per cell
doesn't support one, and this doesn't pretend otherwise.

    python -m benchmarks.cli save-baseline
    python -m benchmarks.cli check-regressions
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from aggregate import per_scenario_config_summary

DEFAULT_BASELINE_PATH = Path(__file__).resolve().parent.parent / "data" / "baseline.json"
METRICS_TO_GUARD = ("response_latency_ms",)  # the assignment's "most important result"
STATS_TO_GUARD = ("p50", "p95")

DEFAULT_ABS_THRESHOLD_MS = 75.0
DEFAULT_PCT_THRESHOLD = 0.12  # 12%


@dataclass
class RegressionFinding:
    scenario_id: str
    config: str
    metric: str
    stat: str
    baseline: float
    current: float
    delta_ms: float
    delta_pct: float


def save_baseline(db_path: Path, baseline_path: Path = DEFAULT_BASELINE_PATH) -> dict:
    summary = per_scenario_config_summary(db_path)
    baseline: dict = {}
    for scenario_id, configs in summary.items():
        baseline[scenario_id] = {}
        for config_label, metrics in configs.items():
            baseline[scenario_id][config_label] = {
                metric: {stat: metrics[metric].get(stat) for stat in STATS_TO_GUARD}
                for metric in METRICS_TO_GUARD
                if metric in metrics
            }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    return baseline


def check_regressions(
    db_path: Path,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    abs_threshold_ms: float = DEFAULT_ABS_THRESHOLD_MS,
    pct_threshold: float = DEFAULT_PCT_THRESHOLD,
) -> list[RegressionFinding]:
    if not baseline_path.exists():
        raise FileNotFoundError(f"no baseline at {baseline_path}; run `save-baseline` first")

    baseline = json.loads(baseline_path.read_text())
    current = per_scenario_config_summary(db_path)

    findings: list[RegressionFinding] = []
    for scenario_id, configs in baseline.items():
        if scenario_id not in current:
            continue
        for config_label, metrics in configs.items():
            if config_label not in current[scenario_id]:
                continue
            for metric in METRICS_TO_GUARD:
                if metric not in metrics:
                    continue
                for stat in STATS_TO_GUARD:
                    base_v = metrics[metric].get(stat)
                    cur_v = current[scenario_id][config_label].get(metric, {}).get(stat)
                    if base_v is None or cur_v is None:
                        continue
                    delta_ms = cur_v - base_v
                    delta_pct = (delta_ms / base_v) if base_v else 0.0
                    if delta_ms > abs_threshold_ms and delta_pct > pct_threshold:
                        findings.append(
                            RegressionFinding(
                                scenario_id=scenario_id,
                                config=config_label,
                                metric=metric,
                                stat=stat,
                                baseline=base_v,
                                current=cur_v,
                                delta_ms=delta_ms,
                                delta_pct=delta_pct,
                            )
                        )
    return findings


def print_regression_report(
    db_path: Path,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    abs_threshold_ms: float = DEFAULT_ABS_THRESHOLD_MS,
    pct_threshold: float = DEFAULT_PCT_THRESHOLD,
) -> bool:
    """Returns True if the run is clean (no regressions)."""
    findings = check_regressions(db_path, baseline_path, abs_threshold_ms, pct_threshold)

    print("=" * 78)
    print(f"REGRESSION CHECK  (baseline: {baseline_path.name}, thresholds: "
          f">{abs_threshold_ms:.0f}ms AND >{pct_threshold * 100:.0f}%)")
    print("=" * 78)

    if not findings:
        print("OK — no regressions past the threshold in any scenario/config.")
        return True

    for f in findings:
        print(
            f"REGRESSION  {f.scenario_id:40s} {f.config:11s} {f.metric}.{f.stat:4s} "
            f"{f.baseline:8.0f}ms -> {f.current:8.0f}ms  (+{f.delta_ms:.0f}ms, +{f.delta_pct * 100:.0f}%)"
        )
    print(f"\n{len(findings)} regression(s) found.")
    return False


if __name__ == "__main__":
    ok = print_regression_report(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/benchmark.sqlite"))
    sys.exit(0 if ok else 1)
