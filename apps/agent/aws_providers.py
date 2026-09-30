"""Wires the official `livekit-plugins-aws` STT/LLM/TTS classes to this
account's real, verified-working services:

- LLM: Bedrock, Amazon Nova Micro via an inference profile (Anthropic
  models on this account are blocked pending an unsubmitted Bedrock "use
  case" form — verified by a live error during setup; Nova Micro is real,
  fast, and already confirmed working via a live Converse call, ~350ms).
- STT: Amazon Transcribe streaming.
- TTS: Amazon Polly, neural engine, same voice as the cached clips.

Deliberately NOT using `aws.realtime.RealtimeModel` (Nova Sonic): that
collapses STT/LLM/TTS into one speech-to-speech call, which would destroy
the granular LLM-TTFT / TTS-first-audio instrumentation the assignment
asks for.

Credentials: this account's `[default]` AWS CLI profile uses a
browser-login credential provider that needs `botocore[crt]` (confirmed by
a live failure during setup). The `wavy` IAM user profile has plain static
keys and needs no extra dependency, so at import time we resolve it once
via boto3 and export it as the standard `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` env vars — the one credential surface every AWS SDK
here (aiobotocore for LLM/TTS, `amazon-transcribe` for STT) reads the same
way, regardless of each library's own internal resolution path. Values are
never printed or logged.
"""
from __future__ import annotations

import os

REGION = "us-east-1"
AWS_PROFILE = "wavy"
LLM_MODEL = "us.amazon.nova-micro-v1:0"   # verified live: real Converse call, ~350ms
TTS_VOICE_ID = "Joanna"
TTS_ENGINE = "neural"
STT_LANGUAGE = "en-US"


def _export_credentials_once() -> None:
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return  # caller already configured the environment explicitly; don't override
    import boto3

    session = boto3.Session(profile_name=AWS_PROFILE)
    frozen = session.get_credentials().get_frozen_credentials()
    os.environ["AWS_ACCESS_KEY_ID"] = frozen.access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = frozen.secret_key
    if frozen.token:
        os.environ["AWS_SESSION_TOKEN"] = frozen.token
    os.environ.setdefault("AWS_DEFAULT_REGION", REGION)


_export_credentials_once()

from livekit.plugins import aws as lk_aws  # noqa: E402


def make_llm() -> "lk_aws.LLM":
    # max_output_tokens keeps replies short and conversational (this is a voice
    # agent, not a chat window) and incidentally guards against the rare
    # runaway-length completion that would otherwise exceed Polly's per-call
    # character limit downstream.
    return lk_aws.LLM(model=LLM_MODEL, region=REGION, max_output_tokens=80)


def make_tts() -> "lk_aws.TTS":
    return lk_aws.TTS(voice=TTS_VOICE_ID, speech_engine=TTS_ENGINE, region=REGION)


def make_stt() -> "lk_aws.STT":
    return lk_aws.STT(language=STT_LANGUAGE, region=REGION)
