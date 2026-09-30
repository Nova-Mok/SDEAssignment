"""P50/P95/mean/min/max aggregation over stored runs.

No statistical-significance claims beyond what the sample size actually
supports — with the default N this is a handful of samples per cell, which
is enough to see a consistent direction/magnitude but not enough to bound
a confidence interval. Said plainly in the README, not hidden here.
"""
from __future__ import annotations

import sqlite3
import statistics
from pathlib import Path


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "mean": None, "p50": None, "p95": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "min": min(values),
        "max": max(values),
    }


METRIC_COLUMNS = [
    "response_latency_ms",
    "llm_ttft_ms",
    "tts_first_audio_ms",
    "backchannel_latency_ms",
    "backchannel_count",
    "cancelled_count",
    "bad_backchannel_count",
    "suppressed_count",
    "overlap_ms",
]


def load_runs(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM runs").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def per_scenario_config_summary(db_path: Path) -> dict[str, dict[str, dict]]:
    """{scenario_id: {config_label: {metric: summary_dict}}}"""
    runs = load_runs(db_path)
    out: dict[str, dict[str, dict]] = {}
    for r in runs:
        scenario_bucket = out.setdefault(r["scenario_id"], {})
        config_bucket = scenario_bucket.setdefault(r["config"], {m: [] for m in METRIC_COLUMNS})
        for m in METRIC_COLUMNS:
            config_bucket[m].append(r[m])

    summarized: dict[str, dict[str, dict]] = {}
    for scenario_id, configs in out.items():
        summarized[scenario_id] = {}
        for config_label, metrics in configs.items():
            summarized[scenario_id][config_label] = {m: summarize(vals) for m, vals in metrics.items()}
    return summarized


def overall_summary(db_path: Path) -> dict[str, dict]:
    """{config_label: {metric: summary_dict}} pooled across all scenarios."""
    runs = load_runs(db_path)
    by_config: dict[str, dict[str, list]] = {}
    for r in runs:
        bucket = by_config.setdefault(r["config"], {m: [] for m in METRIC_COLUMNS})
        for m in METRIC_COLUMNS:
            bucket[m].append(r[m])
    return {config: {m: summarize(vals) for m, vals in metrics.items()} for config, metrics in by_config.items()}


def print_report(db_path: Path) -> None:
    overall = overall_summary(db_path)
    if not overall:
        print(f"No runs found in {db_path}. Run `python -m benchmarks.cli run` first.")
        return

    print("=" * 78)
    print("OVERALL (pooled across all scenarios)")
    print("=" * 78)
    baseline = overall.get("baseline", {})
    backchannel = overall.get("backchannel", {})
    header = f"{'metric':28s} {'baseline':>12s} {'backchannel':>12s} {'delta':>10s}  n(b/bc)"
    print(header)
    for m in ("response_latency_ms", "llm_ttft_ms", "tts_first_audio_ms"):
        b = baseline.get(m, {})
        c = backchannel.get(m, {})
        for stat in ("p50", "p95"):
            bv, cv = b.get(stat), c.get(stat)
            delta = (cv - bv) if (bv is not None and cv is not None) else None
            print(
                f"{m + '.' + stat:28s} {_fmt(bv):>12s} {_fmt(cv):>12s} {_fmt(delta, signed=True):>10s}  "
                f"({b.get('n', 0)}/{c.get('n', 0)})"
            )
    print()
    bc_only = backchannel
    print("Backchannel-only behaviour metrics (pooled):")
    for m in ("backchannel_count", "cancelled_count", "bad_backchannel_count", "suppressed_count", "overlap_ms", "backchannel_latency_ms"):
        s = bc_only.get(m, {})
        print(f"  {m:28s} mean={_fmt(s.get('mean')):>10s} p50={_fmt(s.get('p50')):>10s} max={_fmt(s.get('max')):>10s} n={s.get('n', 0)}")

    print()
    print("=" * 78)
    print("PER SCENARIO (response_latency_ms P50 / P95)")
    print("=" * 78)
    per_scenario = per_scenario_config_summary(db_path)
    for scenario_id, configs in per_scenario.items():
        b = configs.get("baseline", {}).get("response_latency_ms", {})
        c = configs.get("backchannel", {}).get("response_latency_ms", {})
        delta_p50 = (c.get("p50") - b.get("p50")) if (b.get("p50") is not None and c.get("p50") is not None) else None
        print(
            f"{scenario_id:40s} baseline p50={_fmt(b.get('p50')):>8s} p95={_fmt(b.get('p95')):>8s} | "
            f"backchannel p50={_fmt(c.get('p50')):>8s} p95={_fmt(c.get('p95')):>8s} | delta_p50={_fmt(delta_p50, signed=True):>8s}"
        )


def _fmt(v, signed: bool = False) -> str:
    if v is None:
        return "-"
    sign = "+" if signed and v >= 0 else ""
    return f"{sign}{v:.0f}"
