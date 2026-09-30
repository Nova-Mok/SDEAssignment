# Backchanneling for LiveKit Agents

A `BackchannelEngine` that lets a LiveKit voice agent say "mm-hmm" / "okay" / "right" while the user is still talking, without touching the LLM's conversation history, without private LiveKit APIs, and without slowing down the agent's real response.

All numbers below are from a real benchmark against real AWS (Bedrock, Polly, Transcribe) and a real LiveKit Cloud project. Anything scripted rather than measured is labeled.

## Architecture

```
apps/agent/
  backchannel/       BackchannelEngine + state machine + config — pure Python, no LiveKit imports
  livekit_adapter.py  only file that touches real LiveKit objects
  aws_providers.py    real Bedrock (LLM) / Polly (TTS) / Transcribe (STT)
  worker.py           entrypoint — baseline & backchannel modes, same pipeline either way
  cached_clips/       pre-rendered Polly clips
  tests/              15 required behaviors, no live network calls

benchmarks/           10 scenarios, replay runner (real AWS calls), regression checker
apps/web/             Next.js dashboard — Overview / Scenarios / Compare / Run timeline / Live Demo
data/benchmark.sqlite shared DB, written by Python, read by the dashboard
```

```
mic → STT → LLM → TTS → main audio track
       │
       ▼
  BackchannelEngine  →  play()/stop()  →  BackgroundAudioPlayer (its own separate audio track)
```

Backchanneling is a **side branch**, not a pipeline stage — the real response never waits on anything the engine does. That's the whole answer to "does this slow down the real response."

`BackchannelEngine` takes plain data in, calls one `BackchannelAudioProvider` interface out — no LiveKit imports at all, which is what makes it unit-testable without a live connection.

## Decision policy

Every ~200ms, `tick()` estimates an end-of-turn probability (silence gap + trailing punctuation + speech duration) and suppresses unless: the turn's been long enough, cooldown's elapsed, EOT probability is low, not near the end of the turn's silence window, the agent isn't already responding, and the per-turn cap isn't hit. All thresholds live in one config object (env/JSON overridable). Every decision is logged with its reasons.

We don't use LiveKit's own EOT signal — the installed `livekit-agents` wheel has an internal `EotPredictionEvent`/`_AgentBackchannelOpportunityEvent`, but neither is wired to the public event bus (confirmed in source, a literal `# TODO` marks it unreleased). Built our own instead.

## Race conditions & cancellation

The race the assignment names directly — backchannel selected, then the user finishes and the agent must respond — is resolved with **the real response always wins**. Cancellation is real `asyncio.Task.cancel()`, not a flag: the task running a backchannel attempt gets interrupted at whatever await point it's at, mid-TTS-synthesis included, so a stale attempt can't play audio after the conversation has moved on. Every cancellation is logged exactly once, from exactly one place (an earlier version double-logged and briefly broke a soft-cancellation policy; both fixed, both covered by tests).

## Benchmark fairness

10 scripted scenarios, same script for both configs. No live LiveKit room in the loop — that would add WebRTC jitter as a confound between configs — so what's measured is the engine's own overhead plus real Bedrock/Polly latency. Baseline never constructs an engine at all; both modes build the session through the same code path, asserted by a test that diffs the two.

## Results

120 real runs (10 scenarios × 2 configs × 6 runs), run twice on identical code:

| Metric | Run 1 Δ | Run 2 Δ |
|---|---|---|
| Response latency P50 | +15ms | +10ms |
| Response latency P95 | −285ms | +113ms |

That 400ms swing between two runs of the same code is bigger than either delta — it's not a real signal, it's AWS variance. The clearest proof: the scenario with the single largest delta (−95ms) had **zero backchannels fired in either config**, so nothing the engine did could have caused it.

What held in both runs: **zero bad backchannels, zero milliseconds of overlap**, across all 120 backchannel runs. Decision-to-audible latency: 1–34ms.

## Production considerations

- Circuit-break repeated provider failures, not just per-call retries
- Real tracing (span per turn) instead of a sqlite file
- Cancellation guarantee needs to survive a process restart mid-turn, not just a task cancel
- Policy config per customer/locale, not one global process config
- Redact transcripts before persistence for real customer audio

## Bonus

**Cached vs. generated TTS** (`benchmarks/tts_vs_cached.py`, 15 real trials each): cached clip ≈0ms, real Polly synthesis ≈790ms. That's why cached is the default.

**Regression detection** (`benchmarks/regression_check.py`): flags a run only if a change clears both a 75ms *and* a 12% threshold — needed because P95 alone swings 400ms between identical runs (see Results). Verified against both a clean rerun and an injected 400ms regression.

## Credentials (first time only)

You only need these for the live demo or re-running the benchmark yourself. Browsing the dashboard and running the tests need nothing below, `data/benchmark.sqlite` already has real results committed.

**LiveKit** (free):
1. Sign up at [cloud.livekit.io](https://cloud.livekit.io), create a project.
2. From the project settings, copy the URL, API key, and API secret.
3. Put all three into both `apps/agent/.env` and `apps/web/.env.local`:
   ```
   LIVEKIT_URL=wss://your-project.livekit.cloud
   LIVEKIT_API_KEY=...
   LIVEKIT_API_SECRET=...
   ```

**AWS** (Bedrock, Polly, Transcribe):
1. In an AWS account you control, go to the Bedrock console (region `us-east-1`) and request model access for **Amazon Nova Micro**, this is a one-time, usually-instant approval per account.
2. Create an IAM user (or role) with permission to call Bedrock (`InvokeModel`/`Converse`), Polly (`SynthesizeSpeech`), and Transcribe streaming. Generate an access key + secret for it.
3. Export them before running anything:
   ```bash
   export AWS_ACCESS_KEY_ID=...
   export AWS_SECRET_ACCESS_KEY=...
   export AWS_DEFAULT_REGION=us-east-1
   ```
   The code checks for these first and uses them directly, no profile setup needed. (It also knows how to fall back to a named AWS CLI profile called `wavy`, that's specific to how this was originally built and can be ignored.)

## Run it

```bash
# setup
cd apps/agent && python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd ../web && npm install
# credentials: see above

# tests
cd apps/agent && python -m pytest tests/ -v
cd apps/web && npx vitest run

# benchmark (from repo root)
apps/agent/.venv/bin/python -m benchmarks.cli run --n 10
apps/agent/.venv/bin/python -m benchmarks.cli report
apps/agent/.venv/bin/python -m benchmarks.tts_vs_cached --n 15
apps/agent/.venv/bin/python -m benchmarks.cli save-baseline && apps/agent/.venv/bin/python -m benchmarks.cli check-regressions

# dashboard
cd apps/web && npm run dev   # localhost:3000/overview, /scenarios, /compare, /live

# live agent
cd apps/agent && source .venv/bin/activate && python worker.py dev   # then open /live
```
