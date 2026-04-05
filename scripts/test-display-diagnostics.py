#!/usr/bin/env python3
"""Standalone display diagnostics for Raspberry Pi robot-face bring-up."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Draw simple test scenes on the robot display and print SDL diagnostics.",
    )
    parser.add_argument(
        "--fullscreen",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use fullscreen mode (default: true).",
    )
    parser.add_argument(
        "--size",
        default="800x480",
        help="Window size for non-fullscreen mode, formatted as WIDTHxHEIGHT.",
    )
    parser.add_argument(
        "--scene-seconds",
        type=float,
        default=2.0,
        help="How long to hold each auto-cycled scene before moving on.",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="How many auto cycles to run before exiting. 0 means run until quit.",
    )
    parser.add_argument(
        "--driver",
        default="",
        help="Optional SDL_VIDEODRIVER override, for example kmsdrm.",
    )
    parser.add_argument(
        "--screenshot-dir",
        default="",
        help="Optional directory to save one screenshot per scene.",
    )
    return parser.parse_args()


def _parse_size(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except Exception as exc:  # pragma: no cover - defensive CLI parsing
        raise SystemExit(f"invalid --size value: {value!r}") from exc
    if width <= 0 or height <= 0:
        raise SystemExit("--size must contain positive dimensions")
    return width, height


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
    Scene("grid", "Look for a white border, center cross, and corner markers."),
    Scene("circle", "Look for a large white circle with a teal outline in the center."),
    Scene("eyes", "Look for two simple robot eyes in the middle."),
)


def _main() -> int:
    args = _parse_args()
    if args.driver:
        os.environ["SDL_VIDEODRIVER"] = args.driver

    import pygame

    pygame.init()
    pygame.font.init()

    flags = pygame.FULLSCREEN if args.fullscreen else 0
    size = (0, 0) if args.fullscreen else _parse_size(args.size)
    screen = pygame.display.set_mode(size, flags)
    pygame.display.set_caption("AI Companion Robot Display Diagnostics")
    pygame.mouse.set_visible(not args.fullscreen)
    font = pygame.font.Font(None, 30)
    small_font = pygame.font.Font(None, 22)
    clock = pygame.time.Clock()
    screenshot_dir = Path(args.screenshot_dir).expanduser() if args.screenshot_dir else None
    if screenshot_dir is not None:
        screenshot_dir.mkdir(parents=True, exist_ok=True)

    actual_size = screen.get_size()
    print("Display diagnostics")
    print(f"  SDL_VIDEODRIVER env: {os.environ.get('SDL_VIDEODRIVER', '')!r}")
    print(f"  DISPLAY env: {os.environ.get('DISPLAY', '')!r}")
    print(f"  XDG_SESSION_TYPE env: {os.environ.get('XDG_SESSION_TYPE', '')!r}")
    print(f"  WAYLAND_DISPLAY env: {os.environ.get('WAYLAND_DISPLAY', '')!r}")
    print(f"  XDG_RUNTIME_DIR env: {os.environ.get('XDG_RUNTIME_DIR', '')!r}")
    print(f"  pygame version: {pygame.version.ver}")
    print(f"  SDL driver: {pygame.display.get_driver()}")
    print(f"  surface size: {actual_size[0]}x{actual_size[1]}")
    print()
    print("Controls")
    print("  space/right: next scene")
    print("  left: previous scene")
    print("  s: save screenshot")
    print("  q or escape: quit")
    print()

    index = 0
    completed_cycles = 0
    scene_started_at = time.monotonic()
    last_announced_index: int | None = None
    running = True

    while running:
        if last_announced_index != index:
            scene = SCENES[index]
            print(f"[scene {index + 1}/{len(SCENES)}] {scene.name}: {scene.prompt}")
            last_announced_index = index
            if screenshot_dir is not None:
                _save_screenshot(pygame, screen, screenshot_dir, scene.name)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in {pygame.K_q, pygame.K_ESCAPE}:
                    running = False
                elif event.key in {pygame.K_SPACE, pygame.K_RIGHT}:
                    index, completed_cycles = _advance_scene(index, completed_cycles)
                    scene_started_at = time.monotonic()
                elif event.key == pygame.K_LEFT:
                    index = (index - 1) % len(SCENES)
                    scene_started_at = time.monotonic()
                    last_announced_index = None
                elif event.key == pygame.K_s and screenshot_dir is not None:
                    _save_screenshot(pygame, screen, screenshot_dir, SCENES[index].name)

        if args.scene_seconds > 0 and (time.monotonic() - scene_started_at) >= args.scene_seconds:
            index, completed_cycles = _advance_scene(index, completed_cycles)
            scene_started_at = time.monotonic()
            last_announced_index = None
            if args.cycles > 0 and completed_cycles >= args.cycles:
                running = False

        _draw_scene(
            pygame=pygame,
            screen=screen,
            font=font,
            small_font=small_font,
            scene=SCENES[index],
            scene_index=index,
            total_scenes=len(SCENES),
        )
        pygame.display.update()
        clock.tick(30)

    pygame.quit()
    return 0


def _advance_scene(index: int, completed_cycles: int) -> tuple[int, int]:
    next_index = (index + 1) % len(SCENES)
    if next_index == 0:
        completed_cycles += 1
    return next_index, completed_cycles


def _save_screenshot(pygame, screen, screenshot_dir: Path, scene_name: str) -> None:
    path = screenshot_dir / f"{scene_name}.png"
    pygame.image.save(screen, path.as_posix())
    print(f"  saved screenshot: {path}")


def _draw_scene(*, pygame, screen, font, small_font, scene: Scene, scene_index: int, total_scenes: int) -> None:
    width, height = screen.get_size()

    if scene.name == "black":
        screen.fill((0, 0, 0))
    elif scene.name == "white":
        screen.fill((255, 255, 255))
    elif scene.name == "red":
        screen.fill((255, 0, 0))
    elif scene.name == "green":
        screen.fill((0, 255, 0))
    elif scene.name == "blue":
        screen.fill((0, 0, 255))
    else:
        screen.fill((10, 17, 20))
        if scene.name == "grid":
            _draw_grid(pygame, screen, width, height)
        elif scene.name == "circle":
            _draw_circle(pygame, screen, width, height)
        elif scene.name == "eyes":
            _draw_eyes(pygame, screen, width, height)

    _draw_overlay(
        pygame=pygame,
        screen=screen,
        font=font,
        small_font=small_font,
        title=f"Scene {scene_index + 1}/{total_scenes}: {scene.name}",
        message=scene.prompt,
    )


def _draw_grid(pygame, screen, width: int, height: int) -> None:
    white = (245, 245, 245)
    teal = (62, 225, 199)
    pygame.draw.rect(screen, white, pygame.Rect(16, 16, width - 32, height - 32), 4)
    pygame.draw.line(screen, teal, (width // 2, 24), (width // 2, height - 24), 3)
    pygame.draw.line(screen, teal, (24, height // 2), (width - 24, height // 2), 3)
    marker_radius = 18
    for point in ((40, 40), (width - 40, 40), (40, height - 40), (width - 40, height - 40)):
        pygame.draw.circle(screen, white, point, marker_radius, 3)


def _draw_circle(pygame, screen, width: int, height: int) -> None:
    center = (width // 2, height // 2)
    radius = min(width, height) // 4
    pygame.draw.circle(screen, (255, 255, 255), center, radius)
    pygame.draw.circle(screen, (62, 225, 199), center, radius, 10)
    pygame.draw.circle(screen, (255, 179, 71), center, max(12, radius // 5))


def _draw_eyes(pygame, screen, width: int, height: int) -> None:
    eye_fill = (236, 248, 245)
    eye_outline = (62, 225, 199)
    pupil = (17, 40, 43)
    accent = (255, 179, 71)
    eye_width = int(width * 0.24)
    eye_height = int(height * 0.18)
    spacing = int(width * 0.15)
    center_y = height // 2
    left_rect = pygame.Rect(
        (width // 2) - spacing - eye_width,
        center_y - (eye_height // 2),
        eye_width,
        eye_height,
    )
    right_rect = pygame.Rect(
        (width // 2) + spacing,
        center_y - (eye_height // 2),
        eye_width,
        eye_height,
    )
    for rect in (left_rect, right_rect):
        pygame.draw.ellipse(screen, eye_fill, rect)
        pygame.draw.ellipse(screen, eye_outline, rect, 6)
        pupil_center = rect.center
        pygame.draw.circle(screen, pupil, pupil_center, max(10, eye_width // 7))
        pygame.draw.circle(screen, (255, 255, 255), (pupil_center[0] - 10, pupil_center[1] - 10), 6)
        brow_y = rect.top - max(10, eye_height // 4)
        pygame.draw.line(
            screen,
            accent,
            (rect.left + 8, brow_y + 6),
            (rect.right - 8, brow_y - 6),
            4,
        )


def _draw_overlay(*, pygame, screen, font, small_font, title: str, message: str) -> None:
    width, _height = screen.get_size()
    overlay = pygame.Surface((width, 88), pygame.SRCALPHA)
    overlay.fill((4, 12, 14, 210))
    screen.blit(overlay, (0, 0))
    title_surface = font.render(title, True, (255, 255, 255))
    message_surface = small_font.render(message, True, (232, 246, 244))
    screen.blit(title_surface, (20, 14))
    screen.blit(message_surface, (20, 48))


if __name__ == "__main__":
    raise SystemExit(_main())
