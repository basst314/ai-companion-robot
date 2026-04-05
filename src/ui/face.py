"""Procedural robot-face animation state and theme primitives."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field, fields, replace

from shared.events import Event, EventName


@dataclass(slots=True, frozen=True)
class FacePalette:
    background: tuple[int, int, int] = (10, 17, 20)
    eye_fill: tuple[int, int, int] = (236, 248, 245)
    eye_outline: tuple[int, int, int] = (62, 225, 199)
    pupil: tuple[int, int, int] = (17, 40, 43)
    highlight: tuple[int, int, int] = (255, 255, 255)
    accent: tuple[int, int, int] = (255, 179, 71)
    text: tuple[int, int, int] = (232, 246, 244)


@dataclass(slots=True, frozen=True)
class EyeGeometry:
    eye_width_ratio: float = 0.24
    eye_height_ratio: float = 0.19
    eye_spacing_ratio: float = 0.15
    pupil_radius_ratio: float = 0.18
    highlight_radius_ratio: float = 0.05
    outline_width_px: int = 5
    accent_width_px: int = 4


@dataclass(slots=True, frozen=True)
class BlinkTiming:
    interval_min_seconds: float = 2.2
    interval_max_seconds: float = 5.6
    blink_duration_seconds: float = 0.22
    double_blink_chance: float = 0.16
    double_blink_gap_seconds: float = 0.18


@dataclass(slots=True, frozen=True)
class IdleMotionTuning:
    glance_interval_min_seconds: float = 1.6
    glance_interval_max_seconds: float = 4.0
    glance_range_x: float = 0.18
    glance_range_y: float = 0.12
    breathing_period_seconds: float = 5.4
    breathing_bob_amplitude: float = 0.018
    speaking_bob_frequency_hz: float = 4.8
    speaking_bob_amplitude: float = 0.026
    playful_variant_chance: float = 0.22
    playful_variant_duration_seconds: float = 1.2


@dataclass(slots=True, frozen=True)
class TransitionDurations:
    quick_seconds: float = 0.14
    normal_seconds: float = 0.24
    relaxed_seconds: float = 0.42
    sleep_seconds: float = 0.85
    wake_seconds: float = 0.22


@dataclass(slots=True, frozen=True)
class ExpressionPreset:
    name: str
    eye_scale_x: float = 1.0
    eye_scale_y: float = 1.0
    openness_left: float = 1.0
    openness_right: float = 1.0
    pupil_scale_left: float = 1.0
    pupil_scale_right: float = 1.0
    pupil_x_left: float = 0.0
    pupil_x_right: float = 0.0
    pupil_y_left: float = 0.0
    pupil_y_right: float = 0.0
    lid_tilt_left: float = 0.0
    lid_tilt_right: float = 0.0
    brow_left: float = 0.0
    brow_right: float = 0.0
    accent_strength: float = 0.20
    highlight_strength: float = 1.0


@dataclass(slots=True, frozen=True)
class FaceTheme:
    name: str
    palette: FacePalette = field(default_factory=FacePalette)
    geometry: EyeGeometry = field(default_factory=EyeGeometry)
    blink: BlinkTiming = field(default_factory=BlinkTiming)
    idle: IdleMotionTuning = field(default_factory=IdleMotionTuning)
    transitions: TransitionDurations = field(default_factory=TransitionDurations)
    presets: dict[str, ExpressionPreset] = field(default_factory=dict)

    def preset(self, name: str) -> ExpressionPreset:
        if name in self.presets:
            return self.presets[name]
        return self.presets["neutral"]


SUPPORTED_FACE_THEME_NAMES = ("retro_bot", "amber_bot", "noir_bot", "neon_bot")


def build_face_theme(name: str = "retro_bot") -> FaceTheme:
    base_presets = {
        "neutral": ExpressionPreset(
            name="neutral",
            eye_scale_x=1.0,
            eye_scale_y=1.0,
            openness_left=0.96,
            openness_right=0.96,
            accent_strength=0.16,
        ),
        "listening": ExpressionPreset(
            name="listening",
            eye_scale_x=1.04,
            eye_scale_y=1.10,
            openness_left=1.08,
            openness_right=1.08,
            pupil_scale_left=0.94,
            pupil_scale_right=0.94,
            pupil_y_left=-0.02,
            pupil_y_right=-0.02,
            accent_strength=0.34,
        ),
        "thinking": ExpressionPreset(
            name="thinking",
            eye_scale_x=0.98,
            eye_scale_y=0.92,
            openness_left=0.82,
            openness_right=0.96,
            pupil_scale_left=0.92,
            pupil_scale_right=0.88,
            pupil_x_left=-0.04,
            pupil_x_right=0.14,
            pupil_y_left=-0.10,
            pupil_y_right=-0.18,
            lid_tilt_left=0.10,
            lid_tilt_right=0.18,
            brow_left=0.08,
            brow_right=0.14,
            accent_strength=0.28,
        ),
        "responding": ExpressionPreset(
            name="responding",
            eye_scale_x=1.00,
            eye_scale_y=0.98,
            openness_left=0.92,
            openness_right=0.92,
            pupil_y_left=-0.04,
            pupil_y_right=-0.04,
            accent_strength=0.22,
        ),
        "speaking": ExpressionPreset(
            name="speaking",
            eye_scale_x=1.02,
            eye_scale_y=0.98,
            openness_left=0.98,
            openness_right=0.98,
            pupil_scale_left=0.97,
            pupil_scale_right=0.97,
            pupil_y_left=-0.02,
            pupil_y_right=-0.02,
            accent_strength=0.30,
        ),
        "sleepy": ExpressionPreset(
            name="sleepy",
            eye_scale_x=1.00,
            eye_scale_y=0.24,
            openness_left=0.12,
            openness_right=0.12,
            pupil_scale_left=0.84,
            pupil_scale_right=0.84,
            pupil_y_left=0.10,
            pupil_y_right=0.10,
            lid_tilt_left=-0.06,
            lid_tilt_right=0.06,
            brow_left=-0.08,
            brow_right=-0.08,
            accent_strength=0.08,
            highlight_strength=0.45,
        ),
        "playful": ExpressionPreset(
            name="playful",
            eye_scale_x=1.02,
            eye_scale_y=0.94,
            openness_left=0.78,
            openness_right=0.96,
            pupil_scale_left=0.88,
            pupil_scale_right=0.92,
            pupil_x_left=-0.14,
            pupil_x_right=-0.04,
            pupil_y_left=0.02,
            pupil_y_right=-0.02,
            lid_tilt_left=-0.18,
            lid_tilt_right=-0.06,
            brow_left=-0.12,
            brow_right=-0.02,
            accent_strength=0.42,
        ),
        "curious": ExpressionPreset(
            name="curious",
            eye_scale_x=1.03,
            eye_scale_y=1.02,
            openness_left=0.92,
            openness_right=1.06,
            pupil_scale_left=0.92,
            pupil_scale_right=0.88,
            pupil_x_left=0.02,
            pupil_x_right=0.16,
            pupil_y_left=-0.06,
            pupil_y_right=-0.12,
            lid_tilt_left=0.08,
            lid_tilt_right=0.16,
            brow_left=0.06,
            brow_right=0.18,
            accent_strength=0.36,
        ),
    }
    retro_bot = FaceTheme(name="retro_bot", presets=base_presets)
    amber_bot = FaceTheme(
        name="amber_bot",
        palette=FacePalette(
            background=(15, 11, 8),
            eye_fill=(255, 245, 222),
            eye_outline=(255, 177, 66),
            pupil=(48, 28, 12),
            highlight=(255, 250, 240),
            accent=(255, 105, 60),
            text=(255, 239, 212),
        ),
        idle=replace(
            retro_bot.idle,
            breathing_bob_amplitude=0.020,
            playful_variant_chance=0.28,
        ),
        presets={
            **base_presets,
            "neutral": replace(base_presets["neutral"], accent_strength=0.20),
            "listening": replace(base_presets["listening"], accent_strength=0.40),
            "playful": replace(
                base_presets["playful"],
                accent_strength=0.48,
                openness_left=0.74,
                pupil_y_left=0.04,
            ),
        },
    )
    noir_bot = FaceTheme(
        name="noir_bot",
        palette=FacePalette(
            background=(5, 7, 10),
            eye_fill=(230, 238, 246),
            eye_outline=(154, 206, 255),
            pupil=(10, 18, 26),
            highlight=(255, 255, 255),
            accent=(126, 167, 255),
            text=(225, 234, 245),
        ),
        geometry=replace(
            retro_bot.geometry,
            outline_width_px=4,
            accent_width_px=3,
        ),
        idle=replace(
            retro_bot.idle,
            glance_range_x=0.14,
            glance_range_y=0.09,
            playful_variant_chance=0.16,
        ),
        presets={
            **base_presets,
            "neutral": replace(
                base_presets["neutral"],
                eye_scale_x=0.98,
                eye_scale_y=0.94,
                accent_strength=0.12,
            ),
            "thinking": replace(
                base_presets["thinking"],
                openness_left=0.78,
                openness_right=0.90,
                brow_left=0.10,
                brow_right=0.18,
            ),
            "sleepy": replace(
                base_presets["sleepy"],
                highlight_strength=0.32,
                accent_strength=0.05,
            ),
        },
    )
    neon_bot = FaceTheme(
        name="neon_bot",
        palette=FacePalette(
            background=(6, 11, 16),
            eye_fill=(102, 248, 255),
            eye_outline=(73, 241, 255),
            pupil=(6, 28, 36),
            highlight=(210, 255, 255),
            accent=(73, 241, 255),
            text=(188, 250, 255),
        ),
        geometry=replace(
            retro_bot.geometry,
            eye_width_ratio=0.20,
            eye_height_ratio=0.17,
            eye_spacing_ratio=0.13,
            pupil_radius_ratio=0.14,
            outline_width_px=4,
            accent_width_px=3,
        ),
        blink=replace(
            retro_bot.blink,
            interval_min_seconds=2.8,
            interval_max_seconds=5.8,
        ),
        idle=replace(
            retro_bot.idle,
            glance_range_x=0.10,
            glance_range_y=0.07,
            breathing_bob_amplitude=0.010,
            speaking_bob_amplitude=0.014,
            playful_variant_chance=0.14,
        ),
        transitions=replace(
            retro_bot.transitions,
            quick_seconds=0.12,
            normal_seconds=0.20,
            relaxed_seconds=0.34,
        ),
        presets={
            **base_presets,
            "neutral": replace(
                base_presets["neutral"],
                eye_scale_x=0.94,
                eye_scale_y=0.90,
                openness_left=0.98,
                openness_right=0.98,
                pupil_scale_left=0.86,
                pupil_scale_right=0.86,
                accent_strength=0.04,
                highlight_strength=0.84,
            ),
            "listening": replace(
                base_presets["listening"],
                eye_scale_x=0.98,
                eye_scale_y=0.98,
                openness_left=1.02,
                openness_right=1.02,
                pupil_scale_left=0.90,
                pupil_scale_right=0.90,
                accent_strength=0.08,
                highlight_strength=0.90,
            ),
            "thinking": replace(
                base_presets["thinking"],
                eye_scale_x=0.94,
                eye_scale_y=0.84,
                openness_left=0.78,
                openness_right=0.92,
                pupil_scale_left=0.82,
                pupil_scale_right=0.82,
                pupil_x_left=-0.02,
                pupil_x_right=0.10,
                pupil_y_left=-0.08,
                pupil_y_right=-0.12,
                lid_tilt_left=0.04,
                lid_tilt_right=0.12,
                brow_left=0.02,
                brow_right=0.10,
                accent_strength=0.06,
                highlight_strength=0.76,
            ),
            "responding": replace(
                base_presets["responding"],
                eye_scale_x=0.95,
                eye_scale_y=0.88,
                openness_left=0.92,
                openness_right=0.92,
                pupil_scale_left=0.86,
                pupil_scale_right=0.86,
                accent_strength=0.05,
                highlight_strength=0.82,
            ),
            "speaking": replace(
                base_presets["speaking"],
                eye_scale_x=0.92,
                eye_scale_y=0.76,
                openness_left=0.84,
                openness_right=0.84,
                pupil_scale_left=0.80,
                pupil_scale_right=0.80,
                accent_strength=0.05,
                highlight_strength=0.74,
            ),
            "sleepy": replace(
                base_presets["sleepy"],
                eye_scale_x=0.96,
                eye_scale_y=0.20,
                openness_left=0.08,
                openness_right=0.08,
                pupil_scale_left=0.74,
                pupil_scale_right=0.74,
                lid_tilt_left=0.0,
                lid_tilt_right=0.0,
                brow_left=-0.02,
                brow_right=-0.02,
                accent_strength=0.02,
                highlight_strength=0.36,
            ),
            "playful": replace(
                base_presets["playful"],
                eye_scale_x=0.96,
                eye_scale_y=0.88,
                openness_left=0.88,
                openness_right=1.00,
                pupil_scale_left=0.82,
                pupil_scale_right=0.82,
                pupil_x_left=-0.10,
                pupil_x_right=-0.02,
                lid_tilt_left=-0.10,
                lid_tilt_right=-0.04,
                brow_left=-0.04,
                brow_right=0.02,
                accent_strength=0.10,
                highlight_strength=0.82,
            ),
            "curious": replace(
                base_presets["curious"],
                eye_scale_x=0.97,
                eye_scale_y=0.94,
                openness_left=0.92,
                openness_right=1.02,
                pupil_scale_left=0.84,
                pupil_scale_right=0.80,
                pupil_x_left=0.00,
                pupil_x_right=0.12,
                pupil_y_left=-0.04,
                pupil_y_right=-0.10,
                lid_tilt_left=0.04,
                lid_tilt_right=0.12,
                brow_left=0.02,
                brow_right=0.12,
                accent_strength=0.08,
                highlight_strength=0.84,
            ),
        },
    )
    themes = {
        retro_bot.name: retro_bot,
        amber_bot.name: amber_bot,
        noir_bot.name: noir_bot,
        neon_bot.name: neon_bot,
    }
    normalized = (name or "retro_bot").strip().lower()
    if normalized not in themes:
        supported = ", ".join(SUPPORTED_FACE_THEME_NAMES)
        raise ValueError(f"unknown face theme {name!r}; expected one of: {supported}")
    return themes[normalized]


@dataclass(slots=True, frozen=True)
class FacePose:
    eye_scale_x: float = 1.0
    eye_scale_y: float = 1.0
    openness_left: float = 1.0
    openness_right: float = 1.0
    pupil_scale_left: float = 1.0
    pupil_scale_right: float = 1.0
    pupil_x_left: float = 0.0
    pupil_x_right: float = 0.0
    pupil_y_left: float = 0.0
    pupil_y_right: float = 0.0
    lid_tilt_left: float = 0.0
    lid_tilt_right: float = 0.0
    brow_left: float = 0.0
    brow_right: float = 0.0
    bob_y: float = 0.0
    accent_strength: float = 0.0
    highlight_strength: float = 1.0


@dataclass(slots=True, frozen=True)
class FaceFrame:
    scene: str
    expression: str
    overlay_text: str | None
    display_sleep_requested: bool
    pose: FacePose


@dataclass(slots=True)
class FacePresentationState:
    lifecycle: str = "idle"
    emotion: str = "neutral"
    scene: str = "face"
    speech_active: bool = False
    overlay_text: str | None = None
    preview_text: str | None = None
    active_expression: str = "neutral"
    display_sleep_requested: bool = False


@dataclass(slots=True)
class FaceController:
    theme: FaceTheme = field(default_factory=build_face_theme)
    idle_sleep_seconds: float = 300.0
    sleeping_eyes_grace_seconds: float = 12.0
    rng: random.Random = field(default_factory=random.Random)
    state: FacePresentationState = field(default_factory=FacePresentationState)
    current_pose: FacePose = field(default_factory=FacePose)
    _target_pose: FacePose = field(default_factory=FacePose, init=False, repr=False)
    _last_update_at: float = field(default=0.0, init=False, repr=False)
    _last_activity_at: float = field(default=0.0, init=False, repr=False)
    _next_blink_at: float = field(default=0.0, init=False, repr=False)
    _blink_started_at: float | None = field(default=None, init=False, repr=False)
    _next_glance_at: float = field(default=0.0, init=False, repr=False)
    _gaze_target_x: float = field(default=0.0, init=False, repr=False)
    _gaze_target_y: float = field(default=0.0, init=False, repr=False)
    _idle_variant_name: str | None = field(default=None, init=False, repr=False)
    _idle_variant_until: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        now = time.monotonic()
        self._last_update_at = now
        self._last_activity_at = now
        self._schedule_next_blink(now)
        self._schedule_next_glance(now)
        self._target_pose = self.current_pose

    def render_state(self, lifecycle: str, emotion: str, preview_text: str | None = None) -> None:
        now = time.monotonic()
        if (
            lifecycle != self.state.lifecycle
            or emotion != self.state.emotion
            or preview_text != self.state.preview_text
        ):
            self._mark_activity(now)
        self.state.lifecycle = lifecycle
        self.state.emotion = emotion
        self.state.preview_text = preview_text

    def show_text(self, text: str) -> None:
        self._mark_activity(time.monotonic())
        self.state.overlay_text = text

    def clear_text(self) -> None:
        self.state.overlay_text = None

    def show_content(self, mode: str) -> None:
        self._mark_activity(time.monotonic())
        self.state.scene = mode

    def clear_content(self) -> None:
        self.state.scene = "face"

    def handle_event(self, event: Event) -> None:
        now = time.monotonic()
        if event.name is EventName.TTS_PLAYBACK_STARTED:
            self.state.speech_active = True
            self._mark_activity(now)
            return
        if event.name in {EventName.TTS_PLAYBACK_FINISHED, EventName.TTS_INTERRUPTED, EventName.TTS_FAILED}:
            self.state.speech_active = False
            self._mark_activity(now)
            return
        if event.name in {
            EventName.LISTENING_STARTED,
            EventName.TRANSCRIPT_PARTIAL,
            EventName.TRANSCRIPT_FINAL,
            EventName.RESPONSE_READY,
            EventName.FACE_DETECTED,
        }:
            self._mark_activity(now)

    def update(self, now: float | None = None) -> FaceFrame:
        current_time = now if now is not None else time.monotonic()
        dt = max(0.0, current_time - self._last_update_at)
        self._last_update_at = current_time

        self._advance_idle_schedulers(current_time)
        self._update_scene(current_time)
        expression_name = self._resolve_expression(current_time)
        self.state.active_expression = expression_name
        self._target_pose = self._build_target_pose(expression_name, current_time)
        self.current_pose = _blend_pose(
            self.current_pose,
            self._target_pose,
            dt,
            self._transition_duration(expression_name),
        )
        return FaceFrame(
            scene=self.state.scene,
            expression=expression_name,
            overlay_text=self.state.overlay_text,
            display_sleep_requested=self.state.display_sleep_requested,
            pose=self.current_pose,
        )

    def _advance_idle_schedulers(self, now: float) -> None:
        if self.state.scene == "sleep":
            return

        if self._blink_started_at is None and now >= self._next_blink_at:
            self._blink_started_at = now

        if self._blink_started_at is not None:
            blink_elapsed = now - self._blink_started_at
            if blink_elapsed >= self.theme.blink.blink_duration_seconds:
                self._blink_started_at = None
                if self.rng.random() < self.theme.blink.double_blink_chance:
                    self._next_blink_at = now + self.theme.blink.double_blink_gap_seconds
                else:
                    self._schedule_next_blink(now)

        if now >= self._next_glance_at:
            self._gaze_target_x = self.rng.uniform(
                -self.theme.idle.glance_range_x,
                self.theme.idle.glance_range_x,
            )
            self._gaze_target_y = self.rng.uniform(
                -self.theme.idle.glance_range_y,
                self.theme.idle.glance_range_y,
            )
            self._schedule_next_glance(now)
            if (
                self.state.lifecycle == "idle"
                and not self.state.speech_active
                and self.rng.random() < self.theme.idle.playful_variant_chance
            ):
                self._idle_variant_name = self.rng.choice(("playful", "curious"))
                self._idle_variant_until = now + self.theme.idle.playful_variant_duration_seconds

        if self._idle_variant_name is not None and now >= self._idle_variant_until:
            self._idle_variant_name = None

    def _update_scene(self, now: float) -> None:
        if self.state.scene not in {"face", "sleep"}:
            self.state.display_sleep_requested = False
            return

        if self.state.lifecycle != "idle" or self.state.speech_active:
            self.state.scene = "face"
            self.state.display_sleep_requested = False
            return

        idle_seconds = max(0.0, now - self._last_activity_at)
        if idle_seconds < self.idle_sleep_seconds:
            self.state.scene = "face"
            self.state.display_sleep_requested = False
            return

        self.state.scene = "sleep"
        self.state.display_sleep_requested = idle_seconds >= (
            self.idle_sleep_seconds + self.sleeping_eyes_grace_seconds
        )

    def _resolve_expression(self, now: float) -> str:
        if self.state.scene == "sleep":
            return "sleepy"
        if self.state.speech_active or self.state.lifecycle == "speaking":
            return "speaking"
        if self.state.lifecycle == "listening":
            return "listening"
        if self.state.lifecycle == "processing":
            return "thinking"
        if self.state.lifecycle == "responding":
            return "responding"
        if self.state.emotion == "happy":
            return "playful"
        if self.state.emotion in {"curious", "thinking"}:
            return "curious"
        if self._idle_variant_name is not None and now < self._idle_variant_until:
            return self._idle_variant_name
        return "neutral"

    def _build_target_pose(self, expression_name: str, now: float) -> FacePose:
        preset = self.theme.preset(expression_name)
        blink_modifier = self._blink_openness_modifier(now)
        breathing = math.sin((now / self.theme.idle.breathing_period_seconds) * math.tau)
        speaking_bob = 0.0
        if self.state.speech_active:
            speaking_bob = math.sin(now * self.theme.idle.speaking_bob_frequency_hz * math.tau)
        gaze_x = self._gaze_target_x
        gaze_y = self._gaze_target_y
        if expression_name == "sleepy":
            gaze_x = 0.0
            gaze_y = 0.08

        return FacePose(
            eye_scale_x=preset.eye_scale_x,
            eye_scale_y=preset.eye_scale_y,
            openness_left=max(0.04, preset.openness_left * blink_modifier),
            openness_right=max(0.04, preset.openness_right * blink_modifier),
            pupil_scale_left=preset.pupil_scale_left,
            pupil_scale_right=preset.pupil_scale_right,
            pupil_x_left=preset.pupil_x_left + gaze_x,
            pupil_x_right=preset.pupil_x_right + gaze_x,
            pupil_y_left=preset.pupil_y_left + gaze_y,
            pupil_y_right=preset.pupil_y_right + gaze_y,
            lid_tilt_left=preset.lid_tilt_left,
            lid_tilt_right=preset.lid_tilt_right,
            brow_left=preset.brow_left,
            brow_right=preset.brow_right,
            bob_y=(breathing * self.theme.idle.breathing_bob_amplitude)
            + (speaking_bob * self.theme.idle.speaking_bob_amplitude),
            accent_strength=preset.accent_strength,
            highlight_strength=preset.highlight_strength,
        )

    def _blink_openness_modifier(self, now: float) -> float:
        if self._blink_started_at is None or self.state.scene == "sleep":
            return 1.0
        phase = (now - self._blink_started_at) / self.theme.blink.blink_duration_seconds
        if phase <= 0.0 or phase >= 1.0:
            return 1.0
        closedness = 1.0 - abs((phase * 2.0) - 1.0)
        return 1.0 - closedness

    def _schedule_next_blink(self, now: float) -> None:
        self._next_blink_at = now + self.rng.uniform(
            self.theme.blink.interval_min_seconds,
            self.theme.blink.interval_max_seconds,
        )

    def _schedule_next_glance(self, now: float) -> None:
        self._next_glance_at = now + self.rng.uniform(
            self.theme.idle.glance_interval_min_seconds,
            self.theme.idle.glance_interval_max_seconds,
        )

    def _transition_duration(self, expression_name: str) -> float:
        if self.state.scene == "sleep":
            return self.theme.transitions.sleep_seconds
        if expression_name == "listening":
            return self.theme.transitions.quick_seconds
        if self.state.speech_active:
            return self.theme.transitions.quick_seconds
        if expression_name in {"playful", "curious"}:
            return self.theme.transitions.normal_seconds
        return self.theme.transitions.relaxed_seconds

    def _mark_activity(self, now: float) -> None:
        self._last_activity_at = now
        if self.state.display_sleep_requested:
            self.state.display_sleep_requested = False
        if self.state.scene == "sleep":
            self.state.scene = "face"


def _blend_pose(current: FacePose, target: FacePose, dt: float, duration: float) -> FacePose:
    if dt <= 0.0 or duration <= 0.0:
        return target
    alpha = 1.0 - math.exp(-dt / duration)
    values: dict[str, float] = {}
    for pose_field in fields(FacePose):
        current_value = getattr(current, pose_field.name)
        target_value = getattr(target, pose_field.name)
        values[pose_field.name] = current_value + ((target_value - current_value) * alpha)
    return FacePose(**values)
