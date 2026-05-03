"""Tests for the browser-backed face protocol helpers."""

from __future__ import annotations

from shared.config import UiConfig
from shared.events import Event, EventName
from shared.models import ComponentName
from ui.browser_protocol import (
    build_mic_level_command,
    build_overlay_update_command,
    build_renderer_config_command,
    build_renderer_state_command,
    map_event_to_trigger_command,
    normalize_browser_state_override,
)
from ui.face import FacePresentationState


def test_build_renderer_config_command_uses_idle_policy_and_override() -> None:
    config = UiConfig(
        face_idle_enabled=False,
        face_idle_frequency=0.42,
        face_idle_intensity=0.81,
        face_idle_pause_randomness=0.33,
        face_secondary_micro_motion=False,
        face_idle_behaviors=("blink", "unknown", "quick_glance"),
    )

    command = build_renderer_config_command(
        config,
        state_override={
            "baseVisual": {"eyeSize": 0.15},
            "expressionModifiers": {"lookX": 0.1},
        },
    )

    assert command.command_type == "renderer_config"
    assert command.payload["stateOverride"] == {
        "baseVisual": {"eyeSize": 0.15},
        "expressionModifiers": {"lookX": 0.1},
    }
    assert command.payload["idlePolicy"] == {
        "enabled": False,
        "frequency": 0.42,
        "intensity": 0.81,
        "pauseRandomness": 0.33,
        "secondaryMicroMotion": False,
        "allowedBehaviors": ("blink", "quick_glance"),
    }


def test_normalize_browser_state_override_accepts_playground_shapes() -> None:
    raw_state = {
        "baseVisual": {"eyeSize": 0.12},
        "expressionModifiers": {"lookY": -0.2},
    }

    assert normalize_browser_state_override(raw_state) == raw_state
    assert normalize_browser_state_override({"currentState": raw_state}) == raw_state
    assert normalize_browser_state_override({"state": raw_state}) == raw_state
    assert normalize_browser_state_override({"meta": {"name": "bad"}}) is None


def test_renderer_state_command_covers_core_robot_states() -> None:
    cases = [
        (
            "wake_listening",
            FacePresentationState(lifecycle="listening", emotion="listening", scene="face"),
            "face",
            False,
            {
                "lifecycle": "listening",
                "emotion": "listening",
                "speechActive": False,
                "previewText": None,
            },
        ),
        (
            "thinking",
            FacePresentationState(lifecycle="processing", emotion="thinking", scene="face", preview_text="Thinking"),
            "face",
            False,
            {
                "lifecycle": "processing",
                "emotion": "thinking",
                "speechActive": False,
                "previewText": "Thinking",
            },
        ),
        (
            "responding",
            FacePresentationState(lifecycle="responding", emotion="curious", scene="face", preview_text="Answer"),
            "face",
            False,
            {
                "lifecycle": "responding",
                "emotion": "curious",
                "speechActive": False,
                "previewText": "Answer",
            },
        ),
        (
            "speaking",
            FacePresentationState(lifecycle="speaking", emotion="speaking", scene="face", speech_active=True),
            "face",
            False,
            {
                "lifecycle": "speaking",
                "emotion": "speaking",
                "speechActive": True,
                "previewText": None,
            },
        ),
        (
            "sleep",
            FacePresentationState(lifecycle="idle", emotion="neutral", scene="sleep", display_sleep_requested=True),
            "sleep",
            True,
            {
                "lifecycle": "idle",
                "emotion": "neutral",
                "speechActive": False,
                "previewText": None,
            },
        ),
    ]

    for _name, state, scene, display_sleep_requested, expected in cases:
        command = build_renderer_state_command(
            scene=scene,
            display_sleep_requested=display_sleep_requested,
            controller_state=state,
        )

        assert command.command_type == "renderer_state"
        assert command.payload["scene"] == scene
        assert command.payload["displaySleepRequested"] is display_sleep_requested
        assert command.payload["lifecycle"] == expected["lifecycle"]
        assert command.payload["emotion"] == expected["emotion"]
        assert command.payload["speechActive"] is expected["speechActive"]
        assert command.payload["previewText"] == expected["previewText"]


def test_map_event_to_trigger_command_marks_wake_attention_and_speech_scoot() -> None:
    wake_event = Event(
        name=EventName.LISTENING_STARTED,
        source=ComponentName.ORCHESTRATOR,
        payload={"trigger": "wake"},
    )
    follow_up_event = Event(
        name=EventName.LISTENING_STARTED,
        source=ComponentName.ORCHESTRATOR,
        payload={"trigger": "follow_up"},
    )
    audio_event = Event(
        name=EventName.AUDIO_PLAYBACK_STARTED,
        source=ComponentName.AUDIO,
        payload={},
    )

    wake_command = map_event_to_trigger_command(wake_event)
    follow_up_command = map_event_to_trigger_command(follow_up_event)
    audio_command = map_event_to_trigger_command(audio_event)

    assert wake_command is not None
    assert wake_command.payload == {"name": "attention_mode", "reason": "wake_word"}
    assert follow_up_command is not None
    assert follow_up_command.payload == {"name": "quick_glance", "reason": "listening_started"}
    assert audio_command is not None
    assert audio_command.payload == {"name": "scoot", "reason": "speech_started"}


def test_build_overlay_update_command_supports_text_and_rich_content() -> None:
    command = build_overlay_update_command(
        show_text_overlay=True,
        text="Hello Oreo",
        content_mode="status",
        content_payload={
            "title": "Listening",
            "body": "Wake word heard.",
            "icons": ["mic", "spark"],
        },
    )

    assert command.command_type == "overlay_update"
    assert command.payload == {
        "text": "Hello Oreo",
        "contentMode": "status",
        "contentPayload": {
            "title": "Listening",
            "body": "Wake word heard.",
            "icons": ["mic", "spark"],
        },
    }


def test_build_mic_level_command_clamps_normalized_level() -> None:
    assert build_mic_level_command(0.42).as_message() == {
        "type": "mic_level",
        "payload": {"level": 0.42},
    }
    assert build_mic_level_command(-1).payload == {"level": 0.0}
    assert build_mic_level_command(2).payload == {"level": 1.0}
