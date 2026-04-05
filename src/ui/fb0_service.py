"""Framebuffer-backed face UI for Raspberry Pi console displays."""

from __future__ import annotations

import asyncio
import contextlib
import math
import mmap
from dataclasses import dataclass, field
from pathlib import Path

from shared.config import UiConfig
from shared.events import Event
from ui.face import FaceController, FaceFrame, FaceTheme, build_face_theme

_FB0_EYE_SCALE_X = 0.78
_FB0_EYE_SCALE_Y = 0.78
_FB0_SPACING_SCALE = 0.86


@dataclass(slots=True)
class _FramebufferCanvas:
    width: int
    height: int
    stride: int
    buffer: bytearray = field(init=False)

    def __post_init__(self) -> None:
        self.buffer = bytearray(self.stride * self.height)

    def clear(self, color: tuple[int, int, int]) -> None:
        packed = _rgb565(*color)
        row = packed * self.width
        for y in range(self.height):
            start = y * self.stride
            self.buffer[start : start + self.stride] = row

    def set_pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        offset = (y * self.stride) + (x * 2)
        self.buffer[offset : offset + 2] = _rgb565(*color)

    def hline(self, x0: int, x1: int, y: int, color: tuple[int, int, int], *, thickness: int = 1) -> None:
        left = max(0, min(x0, x1))
        right = min(self.width - 1, max(x0, x1))
        if right < left:
            return
        packed = _rgb565(*color)
        segment = packed * (right - left + 1)
        for dy in range(thickness):
            row = y + dy
            if row < 0 or row >= self.height:
                continue
            start = (row * self.stride) + (left * 2)
            self.buffer[start : start + len(segment)] = segment

    def vline(self, x: int, y0: int, y1: int, color: tuple[int, int, int], *, thickness: int = 1) -> None:
        top = max(0, min(y0, y1))
        bottom = min(self.height - 1, max(y0, y1))
        for dx in range(thickness):
            col = x + dx
            if col < 0 or col >= self.width:
                continue
            for y in range(top, bottom + 1):
                self.set_pixel(col, y, color)

    def fill_rect(self, x: int, y: int, width: int, height: int, color: tuple[int, int, int]) -> None:
        for row in range(y, y + height):
            self.hline(x, x + width - 1, row, color)

    def rect(self, x: int, y: int, width: int, height: int, color: tuple[int, int, int], *, thickness: int = 1) -> None:
        self.hline(x, x + width, y, color, thickness=thickness)
        self.hline(x, x + width, y + height, color, thickness=thickness)
        self.vline(x, y, y + height, color, thickness=thickness)
        self.vline(x + width, y, y + height, color, thickness=thickness)

    def line(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: tuple[int, int, int],
        *,
        thickness: int = 1,
    ) -> None:
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            for ox in range(-(thickness // 2), (thickness // 2) + 1):
                for oy in range(-(thickness // 2), (thickness // 2) + 1):
                    self.set_pixel(x0 + ox, y0 + oy, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def fill_ellipse(self, cx: int, cy: int, rx: int, ry: int, color: tuple[int, int, int]) -> None:
        if rx <= 0 or ry <= 0:
            return
        for dy in range(-ry, ry + 1):
            ratio = 1.0 - ((dy * dy) / float(ry * ry))
            if ratio < 0.0:
                continue
            dx = int(rx * (ratio**0.5))
            self.hline(cx - dx, cx + dx, cy + dy, color)

    def ellipse_outline(
        self,
        cx: int,
        cy: int,
        rx: int,
        ry: int,
        color: tuple[int, int, int],
        *,
        thickness: int,
    ) -> None:
        for ring in range(max(1, thickness)):
            self._ellipse_perimeter(cx, cy, max(1, rx - ring), max(1, ry - ring), color)

    def _ellipse_perimeter(self, cx: int, cy: int, rx: int, ry: int, color: tuple[int, int, int]) -> None:
        prev_x: int | None = None
        prev_y: int | None = None
        steps = max(48, int(max(rx, ry) * 3))
        for step in range(steps + 1):
            theta = (step / steps) * 6.283185307179586
            x = cx + int(round(rx * math.cos(theta)))
            y = cy + int(round(ry * math.sin(theta)))
            if prev_x is not None and prev_y is not None:
                self.line(prev_x, prev_y, x, y, color)
            prev_x, prev_y = x, y


@dataclass(slots=True)
class _FramebufferDevice:
    path: Path
    width: int = field(init=False)
    height: int = field(init=False)
    visible_width: int = field(init=False)
    visible_height: int = field(init=False)
    bits_per_pixel: int = field(init=False)
    stride: int = field(init=False)
    _fd: int | None = field(default=None, init=False, repr=False)
    _mmap: mmap.mmap | None = field(default=None, init=False, repr=False)
    _snapshot: bytes | None = field(default=None, init=False, repr=False)

    def open(self) -> None:
        import os

        self.width, self.height = _read_virtual_size(self.path)
        self.visible_width, self.visible_height = _read_visible_size(self.path)
        self.bits_per_pixel = _read_bits_per_pixel(self.path)
        if self.bits_per_pixel != 16:
            raise RuntimeError(f"{self.path} uses {self.bits_per_pixel} bits per pixel; expected 16")
        self.stride = self.width * 2
        self._fd = os.open(self.path, os.O_RDWR)
        self._mmap = mmap.mmap(self._fd, self.stride * self.height)
        self._snapshot = self._mmap[:]

    def write(self, buffer: bytes | bytearray) -> None:
        assert self._mmap is not None
        self._mmap.seek(0)
        self._mmap.write(buffer)
        self._mmap.flush()

    def restore(self) -> None:
        if self._snapshot is not None:
            self.write(self._snapshot)

    def close(self, *, restore: bool = True) -> None:
        import os

        if self._mmap is not None:
            if restore and self._snapshot is not None:
                self._mmap.seek(0)
                self._mmap.write(self._snapshot)
                self._mmap.flush()
            self._mmap.close()
            self._mmap = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


@dataclass(slots=True)
class Fb0FaceUiService:
    """Render the procedural robot face directly to /dev/fb0."""

    config: UiConfig
    theme: FaceTheme = field(default_factory=build_face_theme)
    controller: FaceController = field(init=False)
    _device: _FramebufferDevice | None = field(default=None, init=False, repr=False)
    _canvas: _FramebufferCanvas | None = field(default=None, init=False, repr=False)
    _render_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _stop_requested: bool = field(default=False, init=False, repr=False)
    _display_blanked: bool = field(default=False, init=False, repr=False)
    _sleep_command_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _wake_command_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.controller = FaceController(
            theme=self.theme,
            idle_sleep_seconds=self.config.idle_sleep_seconds,
            sleeping_eyes_grace_seconds=self.config.sleeping_eyes_grace_seconds,
        )

    async def start(self) -> None:
        if self._render_task is not None:
            return
        self._device = _FramebufferDevice(Path(self.config.fb_path))
        self._device.open()
        self._canvas = _FramebufferCanvas(
            width=self._device.width,
            height=self._device.height,
            stride=self._device.stride,
        )
        self._stop_requested = False
        self._draw_frame(self.controller.update())
        self._render_task = asyncio.create_task(self._render_loop())

    async def shutdown(self) -> None:
        self._stop_requested = True
        if self._render_task is not None:
            await self._render_task
            self._render_task = None
        await self._wait_for_command_task(self._sleep_command_task)
        await self._wait_for_command_task(self._wake_command_task)
        if self._device is not None:
            self._device.close(restore=True)
        self._device = None
        self._canvas = None

    async def render_state(self, lifecycle: str, emotion: str, preview_text: str | None = None) -> None:
        self.controller.render_state(lifecycle, emotion, preview_text)

    async def show_text(self, text: str) -> None:
        if self.config.show_text_overlay:
            self.controller.show_text(text)
        else:
            self.controller.clear_text()

    async def show_content(self, mode: str, payload: dict[str, object] | None = None) -> None:
        del payload
        self.controller.show_content(mode)

    async def clear_content(self) -> None:
        self.controller.clear_content()

    async def handle_event(self, event: Event) -> None:
        self.controller.handle_event(event)

    async def _render_loop(self) -> None:
        while not self._stop_requested:
            frame = self.controller.update()
            await self._sync_display_power(frame)
            self._draw_frame(frame)
            fps = self._target_fps(frame)
            await asyncio.sleep(max(0.0, 1.0 / fps))

    async def _sync_display_power(self, frame: FaceFrame) -> None:
        if frame.display_sleep_requested and not self._display_blanked:
            self._display_blanked = True
            if self.config.sleep_command:
                self._sleep_command_task = asyncio.create_task(self._run_command(self.config.sleep_command))
        elif not frame.display_sleep_requested and self._display_blanked:
            self._display_blanked = False
            if self.config.wake_command:
                self._wake_command_task = asyncio.create_task(self._run_command(self.config.wake_command))

    async def _run_command(self, command: tuple[str, ...]) -> None:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()

    async def _wait_for_command_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        with contextlib.suppress(Exception):
            await task

    def _target_fps(self, frame: FaceFrame) -> int:
        if frame.scene == "face" and not frame.display_sleep_requested and self.controller.state.lifecycle != "idle":
            return self.config.active_fps
        if self.controller.state.speech_active:
            return self.config.active_fps
        return self.config.idle_fps

    def _draw_frame(self, frame: FaceFrame) -> None:
        assert self._canvas is not None
        assert self._device is not None
        palette = self.theme.palette
        canvas = self._canvas
        canvas.clear(palette.background)

        if not frame.display_sleep_requested:
            if frame.scene in {"face", "sleep"}:
                self._draw_face(canvas, frame)
            else:
                self._draw_placeholder(canvas, frame.scene)
        self._device.write(canvas.buffer)

    def _draw_face(self, canvas: _FramebufferCanvas, frame: FaceFrame) -> None:
        assert self._device is not None
        width = min(canvas.width, self._device.visible_width)
        height = min(canvas.height, self._device.visible_height)
        geometry = self.theme.geometry
        bob_offset_y = int(frame.pose.bob_y * height)
        eye_width = int(width * geometry.eye_width_ratio * frame.pose.eye_scale_x * _FB0_EYE_SCALE_X)
        eye_height = int(height * geometry.eye_height_ratio * frame.pose.eye_scale_y * _FB0_EYE_SCALE_Y)
        spacing = int(width * geometry.eye_spacing_ratio * _FB0_SPACING_SCALE)
        viewport_left = 0
        viewport_top = 0
        center_y = viewport_top + (height // 2) + bob_offset_y
        center_x = viewport_left + (width // 2)
        left_center_x = center_x - (spacing // 2) - (eye_width // 2)
        right_center_x = center_x + (spacing // 2) + (eye_width // 2)
        self._draw_eye(
            canvas,
            center_x=left_center_x,
            center_y=center_y,
            eye_width=eye_width,
            eye_height=eye_height,
            openness=frame.pose.openness_left,
            pupil_scale=frame.pose.pupil_scale_left,
            pupil_x=frame.pose.pupil_x_left,
            pupil_y=frame.pose.pupil_y_left,
            brow=frame.pose.brow_left,
            lid_tilt=frame.pose.lid_tilt_left,
            accent_strength=frame.pose.accent_strength,
            highlight_strength=frame.pose.highlight_strength,
        )
        self._draw_eye(
            canvas,
            center_x=right_center_x,
            center_y=center_y,
            eye_width=eye_width,
            eye_height=eye_height,
            openness=frame.pose.openness_right,
            pupil_scale=frame.pose.pupil_scale_right,
            pupil_x=frame.pose.pupil_x_right,
            pupil_y=frame.pose.pupil_y_right,
            brow=frame.pose.brow_right,
            lid_tilt=frame.pose.lid_tilt_right,
            accent_strength=frame.pose.accent_strength,
            highlight_strength=frame.pose.highlight_strength,
        )

    def _draw_eye(
        self,
        canvas: _FramebufferCanvas,
        *,
        center_x: int,
        center_y: int,
        eye_width: int,
        eye_height: int,
        openness: float,
        pupil_scale: float,
        pupil_x: float,
        pupil_y: float,
        brow: float,
        lid_tilt: float,
        accent_strength: float,
        highlight_strength: float,
    ) -> None:
        palette = self.theme.palette
        geometry = self.theme.geometry
        visible_height = max(4, int(eye_height * openness))
        rx = max(4, eye_width // 2)
        ry = max(2, visible_height // 2)

        if visible_height <= max(6, eye_height // 7):
            start_x = center_x - rx
            end_x = center_x + rx
            line_y_left = center_y + int(lid_tilt * eye_height * 0.25)
            line_y_right = center_y - int(lid_tilt * eye_height * 0.25)
            canvas.line(
                start_x,
                line_y_left,
                end_x,
                line_y_right,
                palette.eye_outline,
                thickness=max(2, geometry.outline_width_px - 1),
            )
            return

        canvas.fill_ellipse(center_x, center_y, rx, ry, palette.eye_fill)
        canvas.ellipse_outline(
            center_x,
            center_y,
            rx,
            ry,
            palette.eye_outline,
            thickness=geometry.outline_width_px,
        )

        pupil_radius = max(4, int(eye_width * geometry.pupil_radius_ratio * pupil_scale))
        pupil_center_x = center_x + int(pupil_x * (eye_width * 0.22))
        pupil_center_y = center_y + int(pupil_y * (eye_height * 0.22))
        pupil_center_y = max(center_y - ry + pupil_radius, min(center_y + ry - pupil_radius, pupil_center_y))
        canvas.fill_ellipse(pupil_center_x, pupil_center_y, pupil_radius, pupil_radius, palette.pupil)

        if highlight_strength > 0.05:
            highlight_radius = max(2, int(eye_width * geometry.highlight_radius_ratio * highlight_strength))
            canvas.fill_ellipse(
                pupil_center_x - max(2, pupil_radius // 3),
                pupil_center_y - max(2, pupil_radius // 3),
                highlight_radius,
                highlight_radius,
                palette.highlight,
            )

        eyebrow_y = center_y - ry - max(6, int(eye_height * 0.22))
        eyebrow_delta = int((brow + lid_tilt) * eye_height * 0.28)
        canvas.line(
            center_x - rx + 4,
            eyebrow_y + eyebrow_delta,
            center_x + rx - 4,
            eyebrow_y - eyebrow_delta,
            palette.accent,
            thickness=max(2, int(geometry.accent_width_px * max(0.4, accent_strength))),
        )

    def _draw_placeholder(self, canvas: _FramebufferCanvas, scene: str) -> None:
        width = canvas.width
        height = canvas.height
        palette = self.theme.palette
        box_width = max(80, width // 2)
        box_height = max(50, height // 5)
        left = (width - box_width) // 2
        top = (height - box_height) // 2
        canvas.fill_rect(left, top, box_width, box_height, palette.eye_fill)
        canvas.rect(left, top, box_width, box_height, palette.eye_outline, thickness=4)
        if scene != "face":
            canvas.fill_rect(left + 24, top + 20, box_width - 48, 12, palette.accent)


def _read_virtual_size(path: Path) -> tuple[int, int]:
    virtual_size_path = Path("/sys/class/graphics") / path.name / "virtual_size"
    width_text, height_text = virtual_size_path.read_text().strip().split(",", 1)
    return int(width_text), int(height_text)


def _read_visible_size(path: Path) -> tuple[int, int]:
    virtual_width, virtual_height = _read_virtual_size(path)
    drm_root = Path("/sys/class/drm")
    if not drm_root.exists():
        return virtual_width, virtual_height

    for connector in sorted(drm_root.glob("card*-*")):
        status_path = connector / "status"
        if not status_path.exists():
            continue
        if status_path.read_text().strip() != "connected":
            continue
        mode = _read_connector_mode(connector)
        if mode is not None:
            width, height = mode
            return min(width, virtual_width), min(height, virtual_height)

    return virtual_width, virtual_height


def _read_connector_mode(connector: Path) -> tuple[int, int] | None:
    mode_path = connector / "mode"
    mode_text = mode_path.read_text().strip() if mode_path.exists() else ""
    if mode_text:
        parsed = _parse_mode_string(mode_text)
        if parsed is not None:
            return parsed

    modes_path = connector / "modes"
    if not modes_path.exists():
        return None
    for line in modes_path.read_text().splitlines():
        parsed = _parse_mode_string(line.strip())
        if parsed is not None:
            return parsed
    return None


def _parse_mode_string(value: str) -> tuple[int, int] | None:
    if "x" not in value:
        return None
    width_text, height_text = value.lower().split("x", 1)
    digits = []
    for char in height_text:
        if char.isdigit():
            digits.append(char)
        else:
            break
    if not width_text.isdigit() or not digits:
        return None
    return int(width_text), int("".join(digits))


def _read_bits_per_pixel(path: Path) -> int:
    bpp_path = Path("/sys/class/graphics") / path.name / "bits_per_pixel"
    return int(bpp_path.read_text().strip())


def _rgb565(r: int, g: int, b: int) -> bytes:
    value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return value.to_bytes(2, "little")
