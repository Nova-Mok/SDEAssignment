"""Connects a synthetic participant to a real room and speaks a real
monologue (synthesized via Polly) through a real published audio track,
so the real worker.py agent processes real audio end to end — the one
thing the benchmark deliberately does NOT exercise (see README "Benchmark
fairness"). Useful as a scripted live-demo smoke test when no human
microphone is available.

    python scripts/simulate_participant.py backchannel
    python scripts/simulate_participant.py baseline
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import boto3  # noqa: E402
from livekit import api, rtc  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aws_providers import AWS_PROFILE, REGION  # noqa: E402

SAMPLE_RATE = 16000
MONOLOGUE = (
    "So this weekend we drove up to the lake and it was completely packed with people. "
    "We tried to find a spot near the water but everything was taken, so we ended up "
    "setting up further back near the trees. The kids wanted to swim right away but the "
    "water was actually pretty cold, colder than I expected for this time of year. "
    "Anyway we stayed until about sunset and it turned out to be a really nice day overall."
)


def synthesize_monologue() -> bytes:
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=REGION)
    polly = session.client("polly")
    resp = polly.synthesize_speech(
        Text=MONOLOGUE, OutputFormat="pcm", VoiceId="Matthew", Engine="neural", SampleRate=str(SAMPLE_RATE)
    )
    return resp["AudioStream"].read()


async def create_room_and_token(mode: str) -> tuple[str, str, str]:
    import json
    import os
    import time

    room_name = f"sim-{mode}-{int(time.time())}"
    async with api.LiveKitAPI(
        url=os.environ["LIVEKIT_URL"], api_key=os.environ["LIVEKIT_API_KEY"], api_secret=os.environ["LIVEKIT_API_SECRET"]
    ) as lk:
        await lk.room.create_room(api.CreateRoomRequest(name=room_name, metadata=json.dumps({"mode": mode})))

    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("sim-participant")
        .with_grants(api.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True))
        .to_jwt()
    )
    return room_name, token, os.environ["LIVEKIT_URL"]


async def main(mode: str) -> None:
    print(f"Synthesizing monologue via Polly ({len(MONOLOGUE)} chars)...")
    pcm = synthesize_monologue()
    print(f"  {len(pcm)} bytes @ {SAMPLE_RATE}Hz mono ({len(pcm) / 2 / SAMPLE_RATE:.1f}s)")

    room_name, token, url = await create_room_and_token(mode)
    print(f"Created room {room_name!r} (mode={mode}), connecting as sim-participant...")

    room = rtc.Room()
    await room.connect(url, token)

    source = rtc.AudioSource(SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("sim-mic", source)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    print("Published synthetic microphone track. Speaking...")

    frame_ms = 20
    bytes_per_frame = int(SAMPLE_RATE * frame_ms / 1000) * 2
    for i in range(0, len(pcm), bytes_per_frame):
        chunk = pcm[i : i + bytes_per_frame]
        if len(chunk) < bytes_per_frame:
            chunk = chunk + b"\x00" * (bytes_per_frame - len(chunk))
        frame = rtc.AudioFrame(data=chunk, sample_rate=SAMPLE_RATE, num_channels=1, samples_per_channel=len(chunk) // 2)
        await source.capture_frame(frame)

    print("Done speaking. Waiting 6s for the agent's real response, then disconnecting...")
    await asyncio.sleep(6)
    await room.disconnect()
    print(f"room_name={room_name}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "backchannel"
    asyncio.run(main(mode))
