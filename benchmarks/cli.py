"""Benchmark CLI.

    python -m benchmarks.cli run --n 10
    python -m benchmarks.cli run --n 5 --scenario short_answer long_monologue
    python -m benchmarks.cli report

Each (scenario, config) pair runs `--n` times, writing every event and a
per-run summary row into data/benchmark.sqlite (shared with the web UI).
Runs alternate baseline/backchannel per scenario rather than doing all of
one config first, so any slow AWS-side drift over the session's wall-clock
duration affects both configs' distributions roughly equally rather than
skewing one of them.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "agent"))
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from backchannel.config import BackchannelConfig  # noqa: E402
from backchannel.instrumentation import SqliteRecorder  # noqa: E402

from aws_providers import make_llm, make_tts  # noqa: E402

from aggregate import print_report  # noqa: E402
from regression_check import DEFAULT_BASELINE_PATH, print_regression_report, save_baseline  # noqa: E402
from replay_runner import run_one  # noqa: E402
from scenarios import SCENARIOS, SCENARIOS_BY_ID  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "benchmark.sqlite"


async def run_all(n: int, scenario_ids: list[str] | None, db_path: Path) -> None:
    scenarios = [SCENARIOS_BY_ID[s] for s in scenario_ids] if scenario_ids else SCENARIOS
    config = BackchannelConfig.load()
    recorder = SqliteRecorder(db_path)

    # One warm LLM/TTS client shared by every run in this process, both configs —
    # matches a real long-lived AgentSession reusing its clients across turns,
    # and keeps one-time connection setup from leaking into TTFT measurements.
    llm = make_llm()
    tts = make_tts()

    for s in scenarios:
        recorder.upsert_scenario(s.id, s.name, s.description, s.expected_behaviour)

    total = len(scenarios) * n * 2
    done = 0
    t_start = time.time()

    for run_index in range(n):
        for scenario in scenarios:
            for config_label in ("baseline", "backchannel"):
                seed = hash((scenario.id, config_label, run_index)) & 0xFFFFFFFF
                row = await run_one(scenario, config_label, config, recorder, run_index, seed, llm=llm, tts=tts)
                done += 1
                elapsed = time.time() - t_start
                print(
                    f"[{done}/{total}] {scenario.id:40s} {config_label:11s} "
                    f"response={row['response_latency_ms']:.0f}ms "
                    f"llm_ttft={row['llm_ttft_ms']:.0f}ms "
                    f"tts_first_audio={row['tts_first_audio_ms']:.0f}ms "
                    f"bc={row['backchannel_count']} "
                    f"({elapsed:.0f}s elapsed)"
                )

    recorder.close()
    print(f"\nDone. {total} runs written to {db_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="execute the benchmark matrix")
    run_p.add_argument("--n", type=int, default=10, help="runs per (scenario, config) pair")
    run_p.add_argument("--scenario", nargs="*", default=None, help="restrict to these scenario ids")
    run_p.add_argument("--db", type=Path, default=DB_PATH)

    report_p = sub.add_parser("report", help="print P50/P95 aggregate table from an existing DB")
    report_p.add_argument("--db", type=Path, default=DB_PATH)

    baseline_p = sub.add_parser("save-baseline", help="snapshot the current DB's latency as the regression baseline")
    baseline_p.add_argument("--db", type=Path, default=DB_PATH)
    baseline_p.add_argument("--out", type=Path, default=DEFAULT_BASELINE_PATH)

    check_p = sub.add_parser("check-regressions", help="compare the current DB against the saved baseline")
    check_p.add_argument("--db", type=Path, default=DB_PATH)
    check_p.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)

    args = parser.parse_args()
    if args.cmd == "run":
        asyncio.run(run_all(args.n, args.scenario, args.db))
    elif args.cmd == "report":
        print_report(args.db)
    elif args.cmd == "save-baseline":
        save_baseline(args.db, args.out)
        print(f"Baseline saved to {args.out}")
    elif args.cmd == "check-regressions":
        ok = print_regression_report(args.db, args.baseline)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
