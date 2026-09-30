"""Pre-render the cached backchannel clips via Amazon Polly.

Run once (or whenever PHRASES/voice changes):
    python scripts/generate_cached_clips.py

This is deliberately a one-off script, not something the agent calls at
runtime — the whole point of CachedAudioProvider is that these files are
already on disk before a session starts, so serving them has no network
latency. See README "Audio strategy".
"""
from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path

import boto3

AWS_REGION = "us-east-1"
AWS_PROFILE = "wavy"  # static access keys; the account's [default] profile needs botocore[crt]
VOICE_ID = "Joanna"
ENGINE = "neural"
SAMPLE_RATE = 16000

# Plain "mm-hmm" reads oddly on most TTS voices (no phonetic spelling), so we
# nudge a few phrases with light SSML/breaks for a more natural backchannel
# cadence. Anything not listed here is synthesized as plain text.
PHRASES: dict[str, str] = {
    "mm-hmm": '<speak><prosody rate="90%">Mm-hmm.</prosody></speak>',
    "right": '<speak><prosody rate="105%">Right.</prosody></speak>',
    "okay": '<speak><prosody rate="105%">Okay.</prosody></speak>',
    "yeah": '<speak><prosody rate="105%">Yeah.</prosody></speak>',
    "got-it": '<speak><prosody rate="100%">Got it.</prosody></speak>',
}

OUT_DIR = Path(__file__).resolve().parent.parent / "cached_clips"


def synthesize_one(client, phrase: str, ssml: str) -> float:
    resp = client.synthesize_speech(
        Text=ssml,
        TextType="ssml",
        OutputFormat="pcm",
        VoiceId=VOICE_ID,
        Engine=ENGINE,
        SampleRate=str(SAMPLE_RATE),
    )
    pcm_bytes = resp["AudioStream"].read()

    out_path = OUT_DIR / f"{phrase}.wav"
    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_bytes)

    duration_ms = (len(pcm_bytes) / 2 / SAMPLE_RATE) * 1000
    return duration_ms


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    client = session.client("polly")

    durations: dict[str, float] = {}
    for phrase, ssml in PHRASES.items():
        duration_ms = synthesize_one(client, phrase, ssml)
        durations[phrase] = round(duration_ms, 1)
        print(f"  {phrase:10s} -> {OUT_DIR / f'{phrase}.wav'}  ({duration_ms:.0f}ms)")

    manifest_path = OUT_DIR / "durations.json"
    import json

    manifest_path.write_text(json.dumps(durations, indent=2))
    print(f"\nWrote {manifest_path}")


if __name__ == "__main__":
    main()
