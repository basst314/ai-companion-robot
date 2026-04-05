#!/usr/bin/env python3
"""Direct framebuffer display diagnostics for Raspberry Pi console displays."""

from __future__ import annotations

import argparse
import mmap
import os
import time
from dataclasses import dataclass
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write simple diagnostic scenes directly to /dev/fb0.",
    )
    parser.add_argument(
        "--fb-path",
        default="/dev/fb0",
        help="Framebuffer device path (default: /dev/fb0).",
    )
    parser.add_argument(
        "--scene-seconds",
        type=float,
        default=2.0,
        help="Seconds to hold each scene before advancing.",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="How many full cycles to run before exiting. 0 means run forever.",
    )
    parser.add_argument(
        "--restore",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Restore the previous framebuffer contents on exit.",
    )
    return parser.parse_args()


@dataclass(slots=True, frozen=True)
class Scene:
    name: str
    prompt: str


SCENES = (
    Scene("black", "Screen should be fully black."),
    Scene("white", "Screen should be fully white."),
    Scene("red", "Screen should be solid red."),
    Scene("green", "Screen should be solid green."),
    Scene("blue", "Screen should be solid blue."),
    Scene("grid", "Look for a white border, crosshair, and corner markers."),
    Scene("circle", "Look for a large white circle with a teal ring."),
    Scene("eyes", "Look for two simple robot eyes in the middle."),
)


class Framebuffer565:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.fd: int | None = None
        self.mm: mmap.mmap | None = None
        self.width, self.height = _read_virtual_size(self.path)
        self.bits_per_pixel = _read_bits_per_pixel(self.path)
        if self.bits_per_pixel != 16:
            raise RuntimeError(f"{self.path} uses {self.bits_per_pixel} bpp; expected 16 for RGB565")
        self.stride = self.width * 2

    def __enter__(self) -> "Framebuffer565":
        self.fd = os.open(self.path, os.O_RDWR)
        self.mm = mmap.mmap(self.fd, self.stride * self.height)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.mm is not None:
            self.mm.close()
            self.mm = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def snapshot(self) -> bytes:
        assert self.mm is not None
        self.mm.seek(0)
        return self.mm.read(self.stride * self.height)

    def restore(self, content: bytes) -> None:
        assert self.mm is not None
        self.mm.seek(0)
        self.mm.write(content)
        self.mm.flush()

    def clear(self, color: tuple[int, int, int]) -> None:
        packed = _rgb565(*color)
        row = packed * self.width
        assert self.mm is not None
        self.mm.seek(0)
        for _ in range(self.height):
            self.mm.write(row)
        self.mm.flush()

    def set_pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return
        assert self.mm is not None
        offset = (y * self.stride) + (x * 2)
        self.mm[offset : offset + 2] = _rgb565(*color)

    def hline(self, x0: int, x1: int, y: int, color: tuple[int, int, int], thickness: int = 1) -> None:
        for dy in range(thickness):
            for x in range(x0, x1 + 1):
                self.set_pixel(x, y + dy, color)

    def vline(self, x: int, y0: int, y1: int, color: tuple[int, int, int], thickness: int = 1) -> None:
        for dx in range(thickness):
            for y in range(y0, y1 + 1):
                self.set_pixel(x + dx, y, color)

    def rect(self, x: int, y: int, width: int, height: int, color: tuple[int, int, int], thickness: int = 1) -> None:
        self.hline(x, x + width, y, color, thickness)
        self.hline(x, x + width, y + height, color, thickness)
        self.vline(x, y, y + height, color, thickness)
        self.vline(x + width, y, y + height, color, thickness)

    def fill_rect(self, x: int, y: int, width: int, height: int, color: tuple[int, int, int]) -> None:
        for row in range(y, y + height):
            self.hline(x, x + width - 1, row, color, 1)

    def circle(self, cx: int, cy: int, radius: int, color: tuple[int, int, int], fill: bool = False) -> None:
        x = radius
        y = 0
        decision = 1 - x
        while y <= x:
            if fill:
                self.hline(cx - x, cx + x, cy + y, color)
                self.hline(cx - x, cx + x, cy - y, color)
                self.hline(cx - y, cx + y, cy + x, color)
                self.hline(cx - y, cx + y, cy - x, color)
            else:
                for px, py in (
                    (cx + x, cy + y),
                    (cx + y, cy + x),
                    (cx - y, cy + x),
                    (cx - x, cy + y),
                    (cx - x, cy - y),
                    (cx - y, cy - x),
                    (cx + y, cy - x),
                    (cx + x, cy - y),
                ):
                    self.set_pixel(px, py, color)
            y += 1
            if decision <= 0:
                decision += 2 * y + 1
            else:
                x -= 1
                decision += 2 * (y - x) + 1
        assert self.mm is not None
        self.mm.flush()


def _read_virtual_size(path: Path) -> tuple[int, int]:
    virtual_size_path = Path("/sys/class/graphics") / path.name / "virtual_size"
    width_text, height_text = virtual_size_path.read_text().strip().split(",", 1)
    return int(width_text), int(height_text)


def _read_bits_per_pixel(path: Path) -> int:
    bpp_path = Path("/sys/class/graphics") / path.name / "bits_per_pixel"
    return int(bpp_path.read_text().strip())


def _rgb565(r: int, g: int, b: int) -> bytes:
    value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return value.to_bytes(2, "little")


def _draw_scene(fb: Framebuffer565, scene: Scene) -> None:
    black = (0, 0, 0)
    white = (255, 255, 255)
    red = (255, 0, 0)
    green = (0, 255, 0)
    blue = (0, 0, 255)
    teal = (62, 225, 199)
    amber = (255, 179, 71)

    if scene.name == "black":
        fb.clear(black)
        return
    if scene.name == "white":
        fb.clear(white)
        return
    if scene.name == "red":
        fb.clear(red)
        return
    if scene.name == "green":
        fb.clear(green)
        return
    if scene.name == "blue":
        fb.clear(blue)
        return

    fb.clear((10, 17, 20))
    width = fb.width
    height = fb.height

    if scene.name == "grid":
        fb.rect(16, 16, width - 32, height - 32, white, thickness=3)
        fb.vline(width // 2, 24, height - 24, teal, thickness=3)
        fb.hline(24, width - 24, height // 2, teal, thickness=3)
        for x, y in ((40, 40), (width - 40, 40), (40, height - 40), (width - 40, height - 40)):
            fb.circle(x, y, 18, white, fill=False)
        return

    if scene.name == "circle":
        center_x = width // 2
        center_y = height // 2
        radius = min(width, height) // 4
        fb.circle(center_x, center_y, radius, white, fill=True)
        for ring in range(radius, radius - 10, -1):
            fb.circle(center_x, center_y, ring, teal, fill=False)
        fb.circle(center_x, center_y, max(10, radius // 5), amber, fill=True)
        return

    if scene.name == "eyes":
        eye_width = int(width * 0.22)
        eye_height = int(height * 0.16)
        spacing = int(width * 0.15)
        center_y = height // 2
        left_x = (width // 2) - spacing - eye_width
        right_x = (width // 2) + spacing
        for x in (left_x, right_x):
            fb.fill_rect(x, center_y - (eye_height // 2), eye_width, eye_height, white)
            fb.rect(x, center_y - (eye_height // 2), eye_width, eye_height, teal, thickness=4)
            pupil_x = x + (eye_width // 2)
            pupil_y = center_y
            fb.circle(pupil_x, pupil_y, max(10, eye_width // 8), (17, 40, 43), fill=True)
            fb.circle(pupil_x - 10, pupil_y - 10, 5, white, fill=True)
            brow_y = center_y - (eye_height // 2) - max(10, eye_height // 4)
            fb.hline(x + 8, x + eye_width - 8, brow_y, amber, thickness=4)


def _main() -> int:
    args = _parse_args()
    completed_cycles = 0
    scene_index = 0
    previous_scene: str | None = None

    with Framebuffer565(args.fb_path) as fb:
        snapshot = fb.snapshot() if args.restore else b""
        print("Framebuffer diagnostics")
        print(f"  framebuffer: {fb.path}")
        print(f"  size: {fb.width}x{fb.height}")
        print(f"  bits_per_pixel: {fb.bits_per_pixel}")
        print()
        try:
            while True:
                scene = SCENES[scene_index]
                if scene.name != previous_scene:
                    print(f"[scene {scene_index + 1}/{len(SCENES)}] {scene.name}: {scene.prompt}")
                    previous_scene = scene.name
                _draw_scene(fb, scene)
                time.sleep(max(0.1, args.scene_seconds))
                scene_index = (scene_index + 1) % len(SCENES)
                if scene_index == 0:
                    completed_cycles += 1
                    if args.cycles > 0 and completed_cycles >= args.cycles:
                        break
        finally:
            if args.restore and snapshot:
                fb.restore(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
