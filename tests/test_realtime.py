"""Tests for the OpenAI Realtime conversation adapter."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

import main as main_mod
from ai.realtime import (
    AlsaRealtimePcmOutput,
    Pcm16RateConverter,
    RealtimeConversationService,
    RealtimeToolCall,
    RealtimeToolResult,
    _LocalBargeInDetector,
    build_realtime_tool_definitions,
)
from orchestrator.capabilities import build_default_capability_registry
from shared.config import AppConfig
from shared.events import EventName


@dataclass(slots=True)
class _FakeWebSocket:
    incoming: list[dict[str, object]]
    sent: list[dict[str, object]] = field(default_factory=list)
    closed: bool = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if not self.incoming:
            await asyncio.sleep(60)
            return json.dumps({"type": "response.done"})
        return json.dumps(self.incoming.pop(0))

    async def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class _FakePcmOutput:
    chunks: list[bytes] = field(default_factory=list)
    start_calls: int = 0
    interrupt_calls: int = 0
    shutdown_calls: int = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def write(self, pcm_frames: bytes) -> None:
        self.chunks.append(pcm_frames)

    async def interrupt(self) -> None:
        self.interrupt_calls += 1

    async def shutdown(self) -> None:
        self.shutdown_calls += 1

    def is_active(self) -> bool:
        return False


@dataclass(slots=True)
class _PlaybackHoldingPcmOutput(_FakePcmOutput):
    active_checks_remaining: int = 0
    finish_calls: int = 0

    async def finish(self) -> None:
        self.finish_calls += 1

    async def interrupt(self) -> None:
        self.interrupt_calls += 1
        self.active_checks_remaining = 0

    def is_active(self) -> bool:
        if self.active_checks_remaining <= 0:
            return False
        self.active_checks_remaining -= 1
        return True


def test_realtime_service_streams_audio_and_emits_playback_events() -> None:
    output_audio = b"\x01\x00\x02\x00"
    websocket = _FakeWebSocket(
        incoming=[
            {
                "type": "response.output_item.created",
                "item": {"id": "item_1", "type": "message"},
            },
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_1",
                "delta": base64.b64encode(output_audio).decode("ascii"),
            },
            {"type": "response.output_audio.done", "response_id": "resp_1"},
            {"type": "response.done", "response_id": "resp_1"},
        ]
    )
    events = []
    pcm_output = _FakePcmOutput()

    async def run() -> None:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        queue.put_nowait(b"\x10\x00\x20\x00")
        service = RealtimeConversationService(
            api_key="test-key",
            base_url="wss://api.openai.com/v1/realtime",
            model="gpt-realtime-test",
            voice="echo",
            turn_detection="server_vad",
            audio_capture_sample_rate_hz=24000,
            realtime_sample_rate_hz=24000,
            audio_output=pcm_output,
            websocket_factory=lambda url, headers: _return_websocket(url, headers, websocket),
            event_handler=lambda event: _record_event(events, event),
            follow_up_idle_timeout_seconds=0.01,
        )
        await service.run_awake_session(audio_chunks=queue)

    asyncio.run(run())

    assert websocket.closed is True
    assert websocket.sent[0]["type"] == "session.update"
    assert websocket.sent[1]["type"] == "input_audio_buffer.append"
    assert websocket.sent[0]["session"]["audio"]["output"]["voice"] == "echo"
    assert pcm_output.chunks == [output_audio]
    assert [event.name for event in events] == [
        EventName.TTS_PLAYBACK_STARTED,
        EventName.TTS_PLAYBACK_FINISHED,
        EventName.AUDIO_FINISHED,
    ]


def test_realtime_session_update_supports_semantic_vad_auto_eagerness() -> None:
    service = RealtimeConversationService(
        api_key="test-key",
        base_url="wss://api.openai.com/v1/realtime",
        model="gpt-realtime-test",
        voice="echo",
        turn_detection="semantic_vad",
        turn_eagerness="auto",
        audio_capture_sample_rate_hz=24000,
        realtime_sample_rate_hz=24000,
        audio_output=_FakePcmOutput(),
    )

    event = service._session_update_event()

    assert event["session"]["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad",
        "eagerness": "auto",
        "create_response": True,
        "interrupt_response": True,
    }


def test_local_barge_in_detector_requires_sustained_loud_audio() -> None:
    detector = _LocalBargeInDetector(sample_rate_hz=1000, threshold=900, required_speech_ms=100)

    assert detector.detects_barge_in(b"\x00\x08" * 40) is False
    assert detector.detects_barge_in(b"\x00\x08" * 40) is False
    assert detector.detects_barge_in(b"\x00\x08" * 40) is True
    detector.reset()
    assert detector.detects_barge_in(b"\x20\x00" * 200) is False


def test_build_realtime_service_uses_command_audio_output_on_macos(monkeypatch) -> None:
    from ai.realtime import CommandRealtimePcmOutput

    config = AppConfig()
    config.runtime.interaction_backend = "openai_realtime"
    config.runtime.use_mock_ai = False
    config.cloud.enabled = True
    config.cloud.openai_api_key = "test-key"
    config.tts.audio_play_command = ("afplay", "{input_path}")
    monkeypatch.setattr(main_mod.sys, "platform", "darwin")

    service = main_mod._build_realtime_conversation_service(config, build_default_capability_registry())

    assert isinstance(service.audio_output, CommandRealtimePcmOutput)


def test_realtime_function_call_returns_text_and_snapshot_image() -> None:
    websocket = _FakeWebSocket(
        incoming=[
            {
                "type": "response.output_item.created",
                "item": {"id": "item_1", "type": "function_call", "name": "camera_snapshot"},
            },
            {
                "type": "response.function_call_arguments.done",
                "item_id": "item_1",
                "call_id": "call_1",
                "arguments": "{}",
            },
            {"type": "response.done", "response_id": "resp_1"},
        ]
    )

    async def tool_handler(call: RealtimeToolCall) -> RealtimeToolResult:
        assert call.tool_name == "camera_snapshot"
        return RealtimeToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            output_text="I captured the frame.",
            image_url="data:image/png;base64,abc",
        )

    async def run() -> None:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        service = RealtimeConversationService(
            api_key="test-key",
            base_url="wss://api.openai.com/v1/realtime",
            model="gpt-realtime-test",
            voice="echo",
            turn_detection="server_vad",
            audio_capture_sample_rate_hz=24000,
            realtime_sample_rate_hz=24000,
            audio_output=_FakePcmOutput(),
            websocket_factory=lambda url, headers: _return_websocket(url, headers, websocket),
            tool_handler=tool_handler,
            follow_up_idle_timeout_seconds=0.01,
        )
        await service.run_awake_session(audio_chunks=queue)

    asyncio.run(run())

    sent_types = [message["type"] for message in websocket.sent]
    assert sent_types.count("conversation.item.create") == 2
    assert websocket.sent[-1] == {"type": "response.create"}
    assert websocket.sent[-3]["item"]["type"] == "function_call_output"
    assert websocket.sent[-2]["item"]["content"][0]["type"] == "input_image"


def test_realtime_session_keeps_socket_open_for_follow_up_turn_audio() -> None:
    first_audio = b"\x01\x00"
    second_audio = b"\x02\x00"
    websocket = _FakeWebSocket(
        incoming=[
            {
                "type": "response.output_item.created",
                "item": {"id": "item_1", "type": "message"},
            },
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_1",
                "delta": base64.b64encode(first_audio).decode("ascii"),
            },
            {"type": "response.done", "response_id": "resp_1"},
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped"},
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_2",
                "delta": base64.b64encode(second_audio).decode("ascii"),
            },
            {"type": "response.done", "response_id": "resp_2"},
        ]
    )
    pcm_output = _FakePcmOutput()

    async def run() -> None:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        service = RealtimeConversationService(
            api_key="test-key",
            base_url="wss://api.openai.com/v1/realtime",
            model="gpt-realtime-test",
            voice="echo",
            turn_detection="server_vad",
            audio_capture_sample_rate_hz=24000,
            realtime_sample_rate_hz=24000,
            audio_output=pcm_output,
            websocket_factory=lambda url, headers: _return_websocket(url, headers, websocket),
            follow_up_idle_timeout_seconds=0.01,
        )
        await service.run_awake_session(audio_chunks=queue)

    asyncio.run(run())

    assert pcm_output.chunks == [first_audio, second_audio]


def test_realtime_speech_started_cancels_and_ignores_interrupted_response_audio() -> None:
    first_audio = b"\x01\x00" * 2400
    stale_audio = b"\x02\x00"
    reply_audio = b"\x03\x00"
    websocket = _FakeWebSocket(
        incoming=[
            {
                "type": "response.output_item.created",
                "item": {"id": "item_1", "type": "message"},
            },
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_1",
                "delta": base64.b64encode(first_audio).decode("ascii"),
            },
            {"type": "input_audio_buffer.speech_started"},
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_1",
                "delta": base64.b64encode(stale_audio).decode("ascii"),
            },
            {"type": "response.done", "response_id": "resp_1"},
            {"type": "input_audio_buffer.speech_stopped"},
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_2",
                "delta": base64.b64encode(reply_audio).decode("ascii"),
            },
            {"type": "response.output_audio.done", "response_id": "resp_2"},
            {"type": "response.done", "response_id": "resp_2"},
        ]
    )
    events = []
    pcm_output = _FakePcmOutput()

    async def run() -> None:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        service = RealtimeConversationService(
            api_key="test-key",
            base_url="wss://api.openai.com/v1/realtime",
            model="gpt-realtime-test",
            voice="echo",
            turn_detection="server_vad",
            audio_capture_sample_rate_hz=24000,
            realtime_sample_rate_hz=24000,
            audio_output=pcm_output,
            websocket_factory=lambda url, headers: _return_websocket(url, headers, websocket),
            event_handler=lambda event: _record_event(events, event),
            follow_up_idle_timeout_seconds=0.01,
        )
        await service.run_awake_session(audio_chunks=queue)

    asyncio.run(run())

    assert pcm_output.chunks == [first_audio, reply_audio]
    assert pcm_output.interrupt_calls == 1
    sent_types = [message["type"] for message in websocket.sent]
    assert "response.cancel" in sent_types
    assert "conversation.item.truncate" in sent_types
    assert EventName.TTS_INTERRUPTED in [event.name for event in events]


def test_realtime_speech_started_interrupts_local_playback_after_audio_done() -> None:
    first_audio = b"\x01\x00" * 2400
    reply_audio = b"\x03\x00"
    websocket = _FakeWebSocket(
        incoming=[
            {
                "type": "response.output_item.created",
                "item": {"id": "item_1", "type": "message"},
            },
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_1",
                "delta": base64.b64encode(first_audio).decode("ascii"),
            },
            {"type": "response.output_audio.done", "response_id": "resp_1"},
            {"type": "response.done", "response_id": "resp_1"},
            {"type": "input_audio_buffer.speech_started"},
            {"type": "response.done", "response_id": "resp_1"},
            {"type": "input_audio_buffer.speech_stopped"},
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_2",
                "delta": base64.b64encode(reply_audio).decode("ascii"),
            },
            {"type": "response.output_audio.done", "response_id": "resp_2"},
            {"type": "response.done", "response_id": "resp_2"},
        ]
    )
    pcm_output = _PlaybackHoldingPcmOutput(active_checks_remaining=10)

    async def run() -> None:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        service = RealtimeConversationService(
            api_key="test-key",
            base_url="wss://api.openai.com/v1/realtime",
            model="gpt-realtime-test",
            voice="echo",
            turn_detection="server_vad",
            audio_capture_sample_rate_hz=24000,
            realtime_sample_rate_hz=24000,
            audio_output=pcm_output,
            websocket_factory=lambda url, headers: _return_websocket(url, headers, websocket),
            follow_up_idle_timeout_seconds=0.01,
        )
        await service.run_awake_session(audio_chunks=queue)

    asyncio.run(run())

    assert pcm_output.finish_calls == 2
    assert pcm_output.interrupt_calls == 1
    sent_types = [message["type"] for message in websocket.sent]
    assert "response.cancel" not in sent_types
    assert "conversation.item.truncate" in sent_types
    assert pcm_output.chunks == [first_audio, reply_audio]


def test_realtime_follow_up_timeout_waits_for_local_playback_idle() -> None:
    first_audio = b"\x01\x00"
    websocket = _FakeWebSocket(
        incoming=[
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_1",
                "delta": base64.b64encode(first_audio).decode("ascii"),
            },
            {"type": "response.output_audio.done", "response_id": "resp_1"},
            {"type": "response.done", "response_id": "resp_1"},
        ]
    )
    pcm_output = _PlaybackHoldingPcmOutput(active_checks_remaining=2)

    async def run() -> None:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        service = RealtimeConversationService(
            api_key="test-key",
            base_url="wss://api.openai.com/v1/realtime",
            model="gpt-realtime-test",
            voice="echo",
            turn_detection="server_vad",
            audio_capture_sample_rate_hz=24000,
            realtime_sample_rate_hz=24000,
            audio_output=pcm_output,
            websocket_factory=lambda url, headers: _return_websocket(url, headers, websocket),
            follow_up_idle_timeout_seconds=0.01,
        )
        await service.run_awake_session(audio_chunks=queue)

    asyncio.run(run())

    assert pcm_output.finish_calls == 1
    assert pcm_output.interrupt_calls == 0
    assert pcm_output.active_checks_remaining == 0


def test_realtime_session_does_not_apply_absolute_timeout_between_active_turns() -> None:
    first_audio = b"\x01\x00"
    second_audio = b"\x02\x00"
    third_audio = b"\x03\x00"
    websocket = _FakeWebSocket(
        incoming=[
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_1",
                "delta": base64.b64encode(first_audio).decode("ascii"),
            },
            {"type": "response.done", "response_id": "resp_1"},
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped"},
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_2",
                "delta": base64.b64encode(second_audio).decode("ascii"),
            },
            {"type": "response.done", "response_id": "resp_2"},
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped"},
            {
                "type": "response.output_audio.delta",
                "response_id": "resp_3",
                "delta": base64.b64encode(third_audio).decode("ascii"),
            },
            {"type": "response.done", "response_id": "resp_3"},
        ]
    )
    pcm_output = _FakePcmOutput()

    async def run() -> None:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        service = RealtimeConversationService(
            api_key="test-key",
            base_url="wss://api.openai.com/v1/realtime",
            model="gpt-realtime-test",
            voice="echo",
            turn_detection="server_vad",
            audio_capture_sample_rate_hz=24000,
            realtime_sample_rate_hz=24000,
            audio_output=pcm_output,
            websocket_factory=lambda url, headers: _return_websocket(url, headers, websocket),
            follow_up_idle_timeout_seconds=0.01,
        )
        await service.run_awake_session(audio_chunks=queue)

    asyncio.run(run())

    assert pcm_output.chunks == [first_audio, second_audio, third_audio]


def test_realtime_tool_definitions_skip_response_capabilities() -> None:
    registry = build_default_capability_registry()

    tools = build_realtime_tool_definitions(tuple(registry.definitions.values()))

    tool_names = {tool["name"] for tool in tools}
    assert "cloud_reply" not in tool_names
    assert "turn_head" in tool_names
    assert "camera_snapshot" in tool_names
    turn_head = next(tool for tool in tools if tool["name"] == "turn_head")
    assert turn_head["parameters"]["required"] == ["direction"]


def test_pcm16_rate_converter_upsamples_without_audioop_dependency() -> None:
    converter = Pcm16RateConverter(source_rate_hz=16000, target_rate_hz=24000)

    converted = converter.convert(b"\x01\x00\x02\x00\x03\x00\x04\x00")

    assert len(converted) > 8
    assert len(converted) % 2 == 0


def test_alsa_realtime_output_prepends_lead_in_to_each_response(monkeypatch) -> None:
    class FakePcm:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.args = args
            self.kwargs = kwargs
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        def close(self) -> None:
            return None

    handles: list[FakePcm] = []

    def fake_pcm(*args, **kwargs):  # type: ignore[no-untyped-def]
        handle = FakePcm(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setitem(
        sys.modules,
        "alsaaudio",
        SimpleNamespace(
            PCM_PLAYBACK=1,
            PCM_NORMAL=2,
            PCM_FORMAT_S16_LE=3,
            PCM=fake_pcm,
        ),
    )

    async def run() -> None:
        output = AlsaRealtimePcmOutput(
            device="default",
            sample_rate_hz=1000,
            channels=1,
            period_frames=4,
            lead_in_silence_ms=4,
        )
        await output.write(b"\x01\x00")
        await output.write(b"\x02\x00")
        await output.finish()
        await output.write(b"\x03\x00")
        await output.finish()
        await output.shutdown()

    asyncio.run(run())

    assert len(handles) == 1
    assert handles[0].writes == [
        b"\x00" * 8,
        b"\x01\x00\x02\x00" + (b"\x00" * 4),
        b"\x00" * 8,
        b"\x03\x00" + (b"\x00" * 6),
    ]


def test_alsa_realtime_output_recovers_from_device_write_errors(monkeypatch) -> None:
    class FakePcm:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.args = args
            self.kwargs = kwargs
            self.prepare_calls = 0
            self.write_calls = 0
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> None:
            self.write_calls += 1
            if self.write_calls == 1:
                raise RuntimeError("Input/output error [default:CARD=ArrayUAC10]")
            self.writes.append(data)

        def prepare(self) -> None:
            self.prepare_calls += 1

        def close(self) -> None:
            return None

    handles: list[FakePcm] = []

    def fake_pcm(*args, **kwargs):  # type: ignore[no-untyped-def]
        handle = FakePcm(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setitem(
        sys.modules,
        "alsaaudio",
        SimpleNamespace(
            PCM_PLAYBACK=1,
            PCM_NORMAL=2,
            PCM_FORMAT_S16_LE=3,
            PCM=fake_pcm,
        ),
    )

    async def run() -> None:
        output = AlsaRealtimePcmOutput(
            device="default",
            sample_rate_hz=1000,
            channels=1,
            period_frames=4,
            lead_in_silence_ms=0,
        )
        await output.write(b"\x01\x00" * 4)
        await output.finish()
        await asyncio.sleep(0.05)
        assert output._playback_task is not None
        assert not output._playback_task.done()
        await output.shutdown()

    asyncio.run(run())

    assert len(handles) == 1
    assert handles[0].prepare_calls == 1
    assert handles[0].writes == [b"\x01\x00" * 4]


def test_orchestrator_denies_invalid_realtime_tool_arguments() -> None:
    config = AppConfig()
    service = main_mod.build_application(config)

    result = asyncio.run(
        service.handle_realtime_tool_request(
            RealtimeToolCall(
                call_id="call_1",
                tool_name="turn_head",
                arguments={"direction": "upside_down"},
            )
        )
    )

    assert result.call_id == "call_1"
    assert "must be one of" in result.output_text


async def _return_websocket(url: str, headers: object, websocket: _FakeWebSocket) -> _FakeWebSocket:
    assert "model=gpt-realtime-test" in url
    assert headers
    return websocket


async def _record_event(events: list[object], event: object) -> None:
    events.append(event)
