"""Bonus: cached backchannel audio vs. on-demand TTS, measured for real.

Both `CachedAudioProvider` and `TTSAudioProvider` are real components of
`backchannel/audio_provider.py` (not written just for this comparison —
see README "Audio strategy"). This script drives real trials of each
through the exact same `BackchannelAudioProvider.play()` interface the
engine uses, and times decision -> audible for both. The only thing
being compared is where that time goes: a file read (cached) vs. a real
Amazon Polly network round trip (TTS).

    python -m benchmarks.tts_vs_cached --n 15
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "agent"))
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from aws_providers import make_tts  # noqa: E402
from backchannel.audio_provider import CachedAudioProvider, TTSAudioProvider  # noqa: E402

from aggregate import summarize  # noqa: E402

CLIP_DIR = REPO_ROOT / "apps" / "agent" / "cached_clips"
OUT_PATH = REPO_ROOT / "data" / "tts_vs_cached.json"

# Plain text, not the SSML used to render the shipped cached clips — a
# production TTSAudioProvider call is realistically plain text per phrase,
# and this comparison is about scheduling latency, not audio naturalness.
PHRASE_TEXT = {
    "mm-hmm": "Mm-hmm.",
    "right": "Right.",
    "okay": "Okay.",
    "yeah": "Yeah.",
    "got-it": "Got it.",
}


def _load_clip_durations() -> dict[str, float]:
    manifest = CLIP_DIR / "durations.json"
    return json.loads(manifest.read_text()) if manifest.exists() else {}


def make_synthesize_fn():
    tts = make_tts()

    async def synthesize(phrase: str) -> tuple[bytes, float]:
        text = PHRASE_TEXT.get(phrase, phrase)
        stream = tts.synthesize(text)
        chunks: list[bytes] = []
        total_samples = 0
        sample_rate = 16000
        async for audio in stream:
            chunks.append(audio.frame.data)
            total_samples += audio.frame.samples_per_channel
            sample_rate = audio.frame.sample_rate
        await stream.aclose()
        duration_ms = (total_samples / sample_rate) * 1000 if sample_rate else 0.0
        return b"".join(chunks), duration_ms

    return synthesize


async def time_one_play(provider, phrase: str, i: int) -> float:
    t0 = time.monotonic()
    handle = await provider.play(phrase, decision_id=i, turn_id=0)
    elapsed_ms = (time.monotonic() - t0) * 1000
    handle.stop()  # we only care about scheduling latency, not playing the clip out
    return elapsed_ms


async def run_comparison(n: int) -> dict:
    durations = _load_clip_durations()
    phrases = list(durations.keys()) or list(PHRASE_TEXT.keys())

    cached_provider = CachedAudioProvider(clip_dir=CLIP_DIR, clip_duration_ms=durations)
    tts_provider = TTSAudioProvider(phrases=phrases, synthesize=make_synthesize_fn())

    cached_latencies: list[float] = []
    for i in range(n):
        phrase = phrases[i % len(phrases)]
        cached_latencies.append(await time_one_play(cached_provider, phrase, i))

    tts_latencies: list[float] = []
    for i in range(n):
        phrase = phrases[i % len(phrases)]
        tts_latencies.append(await time_one_play(tts_provider, phrase, i))

    result = {
        "n": n,
        "phrases": phrases,
        "cached": {"raw_ms": cached_latencies, "summary": summarize(cached_latencies)},
        "tts": {"raw_ms": tts_latencies, "summary": summarize(tts_latencies)},
    }
    return result


def print_summary(result: dict) -> None:
    c, t = result["cached"]["summary"], result["tts"]["summary"]
    print("=" * 78)
    print(f"CACHED vs TTS backchannel audio, decision -> audible latency (n={result['n']} each)")
    print("=" * 78)
    print(f"{'':20s} {'mean':>10s} {'p50':>10s} {'p95':>10s} {'min':>10s} {'max':>10s}")
    print(f"{'CachedAudioProvider':20s} {c['mean']:10.2f} {c['p50']:10.2f} {c['p95']:10.2f} {c['min']:10.2f} {c['max']:10.2f}")
    print(f"{'TTSAudioProvider':20s} {t['mean']:10.2f} {t['p50']:10.2f} {t['p95']:10.2f} {t['min']:10.2f} {t['max']:10.2f}")
    print()
    diff_ms = t["mean"] - c["mean"]
    # A multiplier against a near-zero cached baseline (a file read, sub-millisecond)
    # is technically correct but reads as an absurd number — the honest framing here
    # is the absolute cost: TTS adds a real network round trip, cached does not.
    print(f"TTS adds ~{diff_ms:.0f}ms of decision-to-audible latency on average that the cached clip does not.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=15)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    result = asyncio.run(run_comparison(args.n))
    print_summary(result)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print(f"\nRaw results written to {args.out}")


if __name__ == "__main__":
    main()
