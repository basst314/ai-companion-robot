"""Typed command helpers for the browser-backed face renderer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from shared.config import UiConfig
from shared.events import Event, EventName
from ui.face import FacePresentationState


_KNOWN_IDLE_BEHAVIORS = frozenset(
    {
        "blink",
        "double_blink",
        "look_side",
        "look_left",
        "look_right",
        "look_up",
        "look_down",
        "quick_glance",
        "bored",
        "curious",
        "cute",
        "thinking",
        "attention_mode",
        "surprise",
        "deadpan_stare",
        "sleep",
        "scoot",
        "boundary_press",
    }
)


@dataclass(slots=True, frozen=True)
class BrowserCommand:
    """One JSON command sent over the browser bridge."""

    command_type: str
    payload: dict[str, object]

    def as_message(self) -> dict[str, object]:
        return {
            "type": self.command_type,
            "payload": self.payload,
        }


def load_browser_state_override(path: Path | None) -> dict[str, object] | None:
    """Load a playground-compatible face override payload from disk."""

    if path is None:
        return None
    raw = json.loads(path.read_text())
    if isinstance(raw, dict):
        normalized = normalize_browser_state_override(raw)
        if normalized is not None:
            return normalized
    raise ValueError(f"browser face state file {path} does not contain a recognized payload shape")


def normalize_browser_state_override(payload: Mapping[str, object]) -> dict[str, object] | None:
    """Accept the same partial state shapes supported by the HTML playground."""

    if _looks_like_face_state(payload):
        return dict(payload)

    current_state = payload.get("currentState")
    if isinstance(current_state, Mapping) and _looks_like_face_state(current_state):
        return dict(current_state)

    saved_state = payload.get("state")
    if isinstance(saved_state, Mapping) and _looks_like_face_state(saved_state):
        return dict(saved_state)

    return None


def build_renderer_config_command(
    config: UiConfig,
    *,
    state_override: dict[str, object] | None = None,
) -> BrowserCommand:
    """Build the initial renderer configuration message."""

    return BrowserCommand(
        command_type="renderer_config",
        payload={
            "stateOverride": state_override,
            "idlePolicy": build_idle_policy_payload(config),
        },
    )


def build_renderer_state_command(
    *,
    scene: str,
    display_sleep_requested: bool,
    controller_state: FacePresentationState,
) -> BrowserCommand:
    """Build a persistent renderer-state message from the UI controller."""

    return BrowserCommand(
        command_type="renderer_state",
        payload={
            "scene": scene,
            "displaySleepRequested": display_sleep_requested,
            "lifecycle": controller_state.lifecycle,
            "emotion": controller_state.emotion,
            "speechActive": controller_state.speech_active,
            "previewText": controller_state.preview_text,
        },
    )


def build_overlay_update_command(
    *,
    show_text_overlay: bool,
    text: str | None,
    content_mode: str,
    content_payload: dict[str, object] | None,
) -> BrowserCommand:
    """Build the current overlay state for the browser renderer."""

    return BrowserCommand(
        command_type="overlay_update",
        payload={
            "text": text if show_text_overlay else None,
            "contentMode": content_mode,
            "contentPayload": content_payload or {},
        },
    )


def build_mic_level_command(level: float) -> BrowserCommand:
    """Build a normalized microphone level update for the browser renderer."""

    clamped = min(1.0, max(0.0, float(level)))
    return BrowserCommand(
        command_type="mic_level",
        payload={"level": clamped},
    )


def build_idle_policy_payload(config: UiConfig) -> dict[str, object]:
    """Translate app config into runtime-tunable idle behavior settings."""

    allowed_behaviors = tuple(
        behavior
        for behavior in config.face_idle_behaviors
        if behavior in _KNOWN_IDLE_BEHAVIORS
    )
    if not allowed_behaviors:
        allowed_behaviors = ("blink",)
    return {
        "enabled": config.face_idle_enabled,
        "frequency": config.face_idle_frequency,
        "intensity": config.face_idle_intensity,
        "pauseRandomness": config.face_idle_pause_randomness,
        "secondaryMicroMotion": config.face_secondary_micro_motion,
        "allowedBehaviors": allowed_behaviors,
    }


def map_event_to_trigger_command(event: Event) -> BrowserCommand | None:
    """Map orchestrator-visible events into transient face behaviors."""

    if event.name is EventName.LISTENING_STARTED:
        trigger = str(event.payload.get("trigger", ""))
        if trigger == "wake":
            return BrowserCommand(
                command_type="transient_trigger",
                payload={
                    "name": "attention_mode",
                    "reason": "wake_word",
                },
            )
        return BrowserCommand(
            command_type="transient_trigger",
            payload={
                "name": "quick_glance",
                "reason": "listening_started",
            },
        )

    if event.name is EventName.AUDIO_PLAYBACK_STARTED:
        return BrowserCommand(
            command_type="transient_trigger",
            payload={
                "name": "scoot",
                "reason": "speech_started",
            },
        )

    return None


def _looks_like_face_state(payload: Mapping[str, object]) -> bool:
    return isinstance(payload.get("baseVisual"), Mapping) and isinstance(
        payload.get("expressionModifiers"),
        Mapping,
    )
