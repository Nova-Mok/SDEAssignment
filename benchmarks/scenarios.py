"""Deterministic replay scenarios.

Each scenario is a scripted timeline of STT-shaped events (interim/final
transcripts at fixed offsets) plus a turn-end offset — exactly what a real
`AgentSession` would feed a listener, but authored by hand so the same
script can be replayed, unmodified, against both configs. Nothing about
timing or text depends on which config is running; that symmetry is the
whole fairness argument (see README).

STT here is explicitly SCRIPTED, not measured — there is no real audio or
Transcribe call in the benchmark loop (see replay_runner.py's docstring for
what *is* real: the LLM and TTS calls that follow).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScenarioEvent:
    offset_ms: float
    kind: str  # "interim" | "final"
    text: str


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    description: str
    events: tuple[ScenarioEvent, ...]
    turn_end_offset_ms: float
    expected_behaviour: str
    prompt_for_llm: str = field(default="")

    @property
    def final_transcript(self) -> str:
        finals = [e.text for e in self.events if e.kind == "final"]
        return finals[-1] if finals else self.prompt_for_llm


def _mk(id_, name, description, events, turn_end_offset_ms, expected_behaviour, prompt_for_llm=""):
    return Scenario(
        id=id_,
        name=name,
        description=description,
        events=tuple(ScenarioEvent(**e) for e in events),
        turn_end_offset_ms=turn_end_offset_ms,
        expected_behaviour=expected_behaviour,
        prompt_for_llm=prompt_for_llm,
    )


SCENARIOS: list[Scenario] = [
    _mk(
        "short_answer",
        "Short answer",
        "User gives a brief, single-word-ish reply. Too short to ever warrant a backchannel.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "yeah"},
            {"offset_ms": 150, "kind": "final", "text": "Yeah, sure."},
        ],
        turn_end_offset_ms=400,
        expected_behaviour="No backchannel: speech duration never crosses MIN_SPEECH_DURATION_MS.",
    ),
    _mk(
        "long_monologue",
        "Long monologue",
        "User talks continuously for several seconds with no pauses, describing a weekend trip.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "so this weekend"},
            {"offset_ms": 400, "kind": "interim", "text": "so this weekend we drove"},
            {"offset_ms": 900, "kind": "interim", "text": "so this weekend we drove up to the lake"},
            {"offset_ms": 1500, "kind": "interim", "text": "so this weekend we drove up to the lake and it was"},
            {"offset_ms": 2100, "kind": "interim", "text": "so this weekend we drove up to the lake and it was completely packed"},
            {"offset_ms": 2700, "kind": "interim", "text": "so this weekend we drove up to the lake and it was completely packed with people"},
            {"offset_ms": 3300, "kind": "final", "text": "So this weekend we drove up to the lake and it was completely packed with people."},
        ],
        turn_end_offset_ms=3500,
        expected_behaviour="At least one backchannel selected once MIN_SPEECH_DURATION_MS is exceeded and EOT probability stays low.",
    ),
    _mk(
        "approaching_eot",
        "User approaching end-of-turn",
        "Speech trails off with sentence-final punctuation and a growing pause, simulating the user winding down.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "I think that covers"},
            {"offset_ms": 500, "kind": "interim", "text": "I think that covers most of it"},
            {"offset_ms": 1100, "kind": "final", "text": "I think that covers most of it."},
        ],
        turn_end_offset_ms=2400,  # long silence gap after the final -> EOT probability should climb
        expected_behaviour="Any candidate near the end of the silence gap is suppressed (near_eot_silence_window / high_eot_probability).",
    ),
    _mk(
        "mid_sentence_pause",
        "Pause in the middle of a sentence",
        "A genuine mid-thought pause (continuation word), not an end-of-turn pause, followed by more speech.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "I wanted to ask you about the report and"},
            {"offset_ms": 700, "kind": "final", "text": "I wanted to ask you about the report and"},
            # pause here - engine may evaluate a candidate during it
            {"offset_ms": 1900, "kind": "interim", "text": "whether we should push the deadline"},
            {"offset_ms": 2500, "kind": "final", "text": "whether we should push the deadline."},
        ],
        turn_end_offset_ms=2900,
        expected_behaviour="A candidate may be selected during the pause (continuation word keeps EOT probability moderate), but the response is unaffected once the user resumes.",
    ),
    _mk(
        "fast_speaker",
        "Fast speaker",
        "Same amount of content as long_monologue but compressed into a much shorter wall-clock window.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "quick update"},
            {"offset_ms": 200, "kind": "interim", "text": "quick update the build is green"},
            {"offset_ms": 400, "kind": "interim", "text": "quick update the build is green and tests pass"},
            {"offset_ms": 600, "kind": "interim", "text": "quick update the build is green and tests pass so we're good to ship"},
            {"offset_ms": 800, "kind": "final", "text": "Quick update, the build is green and tests pass, so we're good to ship."},
        ],
        turn_end_offset_ms=1500,
        expected_behaviour="Decision cadence (MIN_TIME_BETWEEN_DECISIONS_MS) still bounds evaluation frequency even though speech is fast; at most one backchannel given MIN_SPEECH_DURATION_MS.",
    ),
    _mk(
        "noisy_audio",
        "Noisy audio (unstable interim transcripts)",
        "STT keeps revising its interim guess (simulating background noise) before settling on a final.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "the the"},
            {"offset_ms": 250, "kind": "interim", "text": "the systems is"},
            {"offset_ms": 500, "kind": "interim", "text": "the system is is down"},
            {"offset_ms": 750, "kind": "interim", "text": "the system is down in"},
            {"offset_ms": 1000, "kind": "interim", "text": "the system is down in the east region"},
            {"offset_ms": 1300, "kind": "interim", "text": "the system is down in the east region since"},
            {"offset_ms": 1600, "kind": "interim", "text": "the system is down in the east region since about noon"},
            {"offset_ms": 1950, "kind": "final", "text": "The system is down in the east region since about noon."},
        ],
        turn_end_offset_ms=2300,
        expected_behaviour="Churn in interim text must not itself trigger extra async work — decisions are still rate-limited by MIN_TIME_BETWEEN_DECISIONS_MS.",
    ),
    _mk(
        "long_speech_multiple_opportunities",
        "Long speech, multiple potential backchannels",
        "A long, information-dense turn with several natural micro-pauses, long enough for the per-turn cap to matter.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "let me walk you through the whole incident"},
            {"offset_ms": 600, "kind": "final", "text": "Let me walk you through the whole incident."},
            {"offset_ms": 2200, "kind": "interim", "text": "it started around 2am with a spike in error rates"},
            {"offset_ms": 2900, "kind": "final", "text": "It started around 2am with a spike in error rates."},
            {"offset_ms": 4600, "kind": "interim", "text": "we rolled back the deploy and it recovered by 3am"},
            {"offset_ms": 5300, "kind": "final", "text": "We rolled back the deploy and it recovered by 3am."},
            {"offset_ms": 7000, "kind": "interim", "text": "and we're writing up the postmortem now"},
            {"offset_ms": 7600, "kind": "final", "text": "And we're writing up the postmortem now."},
        ],
        turn_end_offset_ms=8000,
        expected_behaviour="Multiple backchannels selected across the turn, capped at MAX_BACKCHANNELS_PER_TURN, each respecting cooldown.",
    ),
    _mk(
        "user_stops_as_backchannel_about_to_play",
        "User stops just as a backchannel is about to play",
        "Speech continues just long enough to trigger a SELECT, then the user's turn ends almost immediately after — racing the decision against EOT.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "one more thing about the invoice"},
            {"offset_ms": 500, "kind": "interim", "text": "one more thing about the invoice from last month"},
            {"offset_ms": 1000, "kind": "final", "text": "One more thing about the invoice from last month."},
        ],
        turn_end_offset_ms=1250,  # ~1 tick after MIN_SPEECH_DURATION_MS is first crossed
        expected_behaviour="The race the assignment calls out directly: any backchannel selected right before turn-end must be cancelled before or immediately as it becomes audible, per CANCEL_ON_EOT.",
    ),
    _mk(
        "cooldown_repeated_ack",
        "Cooldown / repeated acknowledgement pressure",
        "Several long, evenly-spaced continuation points designed to repeatedly re-cross the backchannel threshold, stress-testing cooldown spacing.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "okay so first"},
            {"offset_ms": 400, "kind": "final", "text": "Okay so first,"},
            {"offset_ms": 1900, "kind": "interim", "text": "second"},
            {"offset_ms": 2200, "kind": "final", "text": "second,"},
            {"offset_ms": 3700, "kind": "interim", "text": "third"},
            {"offset_ms": 4000, "kind": "final", "text": "third,"},
            {"offset_ms": 5500, "kind": "interim", "text": "and finally"},
            {"offset_ms": 5900, "kind": "final", "text": "and finally, that's everything."},
        ],
        turn_end_offset_ms=6300,
        expected_behaviour="Backchannels are spaced at least BACKCHANNEL_COOLDOWN_MS apart even though multiple candidates arise; none fire back-to-back.",
    ),
    _mk(
        "agent_response_starts_immediately",
        "Agent must respond immediately, no added delay",
        "A short-ish but valid turn ending cleanly, used specifically to measure response_latency in the simplest possible case with no backchannel-induced complexity.",
        [
            {"offset_ms": 0, "kind": "interim", "text": "what time is the meeting tomorrow"},
            {"offset_ms": 600, "kind": "final", "text": "What time is the meeting tomorrow?"},
        ],
        turn_end_offset_ms=900,
        expected_behaviour="response_latency (user_speech_end -> agent audio start) should be statistically indistinguishable between baseline and backchannel for this scenario.",
    ),
]

SCENARIOS_BY_ID: dict[str, Scenario] = {s.id: s for s in SCENARIOS}
