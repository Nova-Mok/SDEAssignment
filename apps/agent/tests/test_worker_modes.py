"""Behavior #15: backchannel-enabled mode must not change baseline
behaviour when disabled. Concretely: baseline mode must build the exact
same AgentSession pipeline as backchannel mode (same STT/LLM/TTS/turn
handling), and must never construct a BackgroundAudioPlayer or
BackchannelEngine — the backchannel-only code path in worker.py must be
unreachable when mode == "baseline".

No real network/AWS/LiveKit connection here: AgentSession, Agent, and
BackgroundAudioPlayer are all replaced with recording fakes, and
`_read_mode` is exercised directly against fake room objects.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import worker


class _FakeRoom:
    def __init__(self, metadata: str, name: str = "room-1"):
        self.metadata = metadata
        self.name = name


class _FakeJobContext:
    def __init__(self, metadata: str):
        self.room = _FakeRoom(metadata)
        self.connect = AsyncMock()
        self._shutdown_callbacks = []

    def add_shutdown_callback(self, cb):
        self._shutdown_callbacks.append(cb)


class _FakeAgentSession:
    """Records constructor kwargs and .on() subscriptions; start() is a no-op."""

    instances: list["_FakeAgentSession"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.listeners: dict[str, list] = {}
        _FakeAgentSession.instances.append(self)

    def on(self, event_name, handler):
        self.listeners.setdefault(event_name, []).append(handler)

    async def start(self, agent, *, room):
        self.started_with = {"agent": agent, "room": room}


@pytest.fixture(autouse=True)
def _reset_fake_session():
    _FakeAgentSession.instances.clear()
    yield
    _FakeAgentSession.instances.clear()


def test_read_mode_defaults_to_baseline_when_missing_or_malformed():
    assert worker._read_mode(_FakeJobContext("")) == "baseline"
    assert worker._read_mode(_FakeJobContext("not json")) == "baseline"
    assert worker._read_mode(_FakeJobContext('{"mode": "nonsense"}')) == "baseline"
    assert worker._read_mode(_FakeJobContext('{"mode": "backchannel"}')) == "backchannel"
    assert worker._read_mode(_FakeJobContext('{"mode": "baseline"}')) == "baseline"


@pytest.mark.asyncio
async def test_baseline_mode_never_touches_backchannel_machinery():
    ctx = _FakeJobContext('{"mode": "baseline"}')

    with (
        patch("worker.AgentSession", _FakeAgentSession),
        patch("worker.Agent", lambda **kw: SimpleNamespace(**kw)),
        patch("worker.BackgroundAudioPlayer") as mock_bg_player,
        patch("worker.LiveKitCachedAudioProvider") as mock_provider,
        patch("worker.BackchannelEngine") as mock_engine,
        patch("worker.wire_session_events") as mock_wire,
    ):
        await worker.entrypoint(ctx)

        mock_bg_player.assert_not_called()
        mock_provider.assert_not_called()
        mock_engine.assert_not_called()
        mock_wire.assert_not_called()

    assert len(_FakeAgentSession.instances) == 1
    session = _FakeAgentSession.instances[0]
    assert session.listeners == {}  # no engine wiring subscribed anything
    assert session.kwargs["turn_detection"] == "stt"


@pytest.mark.asyncio
async def test_backchannel_mode_builds_identical_session_plus_engine():
    ctx = _FakeJobContext('{"mode": "backchannel"}')

    fake_bg_instance = SimpleNamespace(start=AsyncMock(), aclose=AsyncMock())

    with (
        patch("worker.AgentSession", _FakeAgentSession),
        patch("worker.Agent", lambda **kw: SimpleNamespace(**kw)),
        patch("worker.BackgroundAudioPlayer", return_value=fake_bg_instance) as mock_bg_player,
        patch("worker.LiveKitCachedAudioProvider") as mock_provider,
        patch("worker.BackchannelEngine") as mock_engine_cls,
        patch("worker.wire_session_events") as mock_wire,
    ):
        mock_engine_cls.return_value.run = AsyncMock()

        await worker.entrypoint(ctx)

        mock_bg_player.assert_called_once()
        fake_bg_instance.start.assert_awaited_once()
        mock_provider.assert_called_once()
        mock_engine_cls.assert_called_once()
        mock_wire.assert_called_once()
        mock_engine_cls.return_value.run.assert_awaited_once()

    assert len(_FakeAgentSession.instances) == 1
    baseline_kwargs = _FakeAgentSession.instances[0].kwargs
    assert baseline_kwargs["turn_detection"] == "stt"


@pytest.mark.asyncio
async def test_both_modes_build_the_same_pipeline_shape():
    """The only difference between the two entrypoint runs must be the
    backchannel block — same STT/LLM/TTS/turn_detection either way."""
    captured_kwargs: list[dict] = []

    class _CapturingSession(_FakeAgentSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            captured_kwargs.append({k: type(v).__name__ for k, v in kwargs.items()})

    for mode in ("baseline", "backchannel"):
        ctx = _FakeJobContext(f'{{"mode": "{mode}"}}')
        with (
            patch("worker.AgentSession", _CapturingSession),
            patch("worker.Agent", lambda **kw: SimpleNamespace(**kw)),
            patch("worker.BackgroundAudioPlayer", return_value=SimpleNamespace(start=AsyncMock(), aclose=AsyncMock())),
            patch("worker.LiveKitCachedAudioProvider"),
            patch("worker.BackchannelEngine") as mock_engine_cls,
            patch("worker.wire_session_events"),
        ):
            mock_engine_cls.return_value.run = AsyncMock()
            await worker.entrypoint(ctx)

    assert captured_kwargs[0] == captured_kwargs[1], "baseline and backchannel must build the same session shape"
