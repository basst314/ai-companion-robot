"""Tests for the procedural robot face controller."""

from __future__ import annotations

import random

import pytest

from shared.events import Event, EventName
from shared.models import ComponentName
from ui.face import SUPPORTED_FACE_THEME_NAMES, FaceController, build_face_theme


def _make_controller(*, idle_sleep_seconds: float = 30.0, grace_seconds: float = 8.0) -> FaceController:
    controller = FaceController(
        idle_sleep_seconds=idle_sleep_seconds,
        sleeping_eyes_grace_seconds=grace_seconds,
        rng=random.Random(7),
    )
    baseline = 100.0
    controller._last_update_at = baseline
    controller._last_activity_at = baseline
    controller._next_blink_at = baseline + 0.10
    controller._next_glance_at = baseline + 0.10
    return controller


def test_face_controller_blends_smoothly_into_listening_state() -> None:
    controller = _make_controller()
    neutral_frame = controller.update(100.0)

    controller.render_state("listening", "listening")
    listening_frame = controller.update(100.05)

    assert listening_frame.expression == "listening"
    assert neutral_frame.pose.openness_left < listening_frame.pose.openness_left < 1.08
    assert neutral_frame.pose.accent_strength < listening_frame.pose.accent_strength


def test_face_controller_idle_animation_introduces_blinks_and_glances() -> None:
    controller = _make_controller()
    initial_frame = controller.update(100.0)
    controller.update(100.22)
    animated_frame = controller.update(100.33)

    assert animated_frame.pose.openness_left < initial_frame.pose.openness_left
    assert animated_frame.pose.pupil_x_left != initial_frame.pose.pupil_x_left


def test_face_controller_enters_sleep_scene_before_requesting_display_sleep() -> None:
    controller = _make_controller(idle_sleep_seconds=10.0, grace_seconds=5.0)
    controller.update(100.0)

    sleepy_frame = controller.update(110.1)
    sleeping_display_frame = controller.update(115.2)

    assert sleepy_frame.scene == "sleep"
    assert sleepy_frame.expression == "sleepy"
    assert sleepy_frame.display_sleep_requested is False
    assert sleeping_display_frame.display_sleep_requested is True


def test_face_controller_wakes_from_sleep_on_activity() -> None:
    controller = _make_controller(idle_sleep_seconds=10.0, grace_seconds=1.0)
    controller.update(100.0)
    controller.update(111.5)

    controller.render_state("listening", "listening")
    frame = controller.update(111.6)

    assert frame.scene == "face"
    assert frame.display_sleep_requested is False
    assert frame.expression == "listening"


def test_face_controller_speaking_animation_follows_tts_events() -> None:
    controller = _make_controller()
    controller.render_state("responding", "curious")
    responding_frame = controller.update(100.0)

    controller.handle_event(
        Event(
            name=EventName.TTS_PLAYBACK_STARTED,
            source=ComponentName.TTS,
            payload={},
        )
    )
    speaking_frame = controller.update(100.1)

    controller.handle_event(
        Event(
            name=EventName.TTS_PLAYBACK_FINISHED,
            source=ComponentName.TTS,
            payload={},
        )
    )
    post_speaking_frame = controller.update(100.2)

    assert responding_frame.expression == "responding"
    assert speaking_frame.expression == "speaking"
    assert speaking_frame.pose.bob_y != 0.0
    assert post_speaking_frame.expression == "responding"


def test_build_face_theme_supports_multiple_personalities() -> None:
    retro = build_face_theme("retro_bot")
    amber = build_face_theme("amber_bot")
    neon = build_face_theme("neon_bot")

    assert "amber_bot" in SUPPORTED_FACE_THEME_NAMES
    assert "neon_bot" in SUPPORTED_FACE_THEME_NAMES
    assert amber.name == "amber_bot"
    assert neon.name == "neon_bot"
    assert amber.palette.eye_outline != retro.palette.eye_outline
    assert amber.presets["playful"].accent_strength > retro.presets["playful"].accent_strength
    assert neon.palette.eye_outline != retro.palette.eye_outline
    assert neon.geometry.eye_width_ratio < retro.geometry.eye_width_ratio


def test_build_face_theme_rejects_unknown_theme_name() -> None:
    with pytest.raises(ValueError, match="unknown face theme"):
        build_face_theme("mystery_bot")
