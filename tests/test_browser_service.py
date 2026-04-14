"""Tests for browser renderer service helpers."""

from __future__ import annotations

from shared.config import UiConfig
from ui.browser_service import _build_browser_command, _build_ws_accept, _write_no_cache_headers


def test_build_browser_command_for_windowed_launch_uses_app_mode() -> None:
    command = _build_browser_command(
        config=UiConfig(
            browser_launch_mode="windowed",
            browser_executable="/opt/chrome",
            browser_profile_dir=None,
            browser_extra_args=("--disable-gpu",),
        ),
        url="http://127.0.0.1:8765/robot-face-runtime.html?ws=8766",
    )

    assert command == [
        "/opt/chrome",
        "--window-size=800,480",
        "--app=http://127.0.0.1:8765/robot-face-runtime.html?ws=8766",
        "--disable-gpu",
    ]


def test_build_browser_command_for_kiosk_launch_places_url_last() -> None:
    command = _build_browser_command(
        config=UiConfig(
            browser_launch_mode="kiosk",
            browser_executable="/opt/chromium",
            browser_profile_dir=None,
            browser_extra_args=("--disable-gpu", "--force-device-scale-factor=1"),
        ),
        url="http://127.0.0.1:8765/robot-face-runtime.html?ws=8766",
    )

    assert command == [
        "/opt/chromium",
        "--kiosk",
        "--start-fullscreen",
        "--noerrdialogs",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--disable-gpu",
        "--force-device-scale-factor=1",
        "http://127.0.0.1:8765/robot-face-runtime.html?ws=8766",
    ]


def test_build_ws_accept_matches_rfc_example() -> None:
    assert _build_ws_accept("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_write_no_cache_headers_sets_browser_cache_controls() -> None:
    headers: list[tuple[str, str]] = []

    class _Response:
        def send_header(self, key: str, value: str) -> None:
            headers.append((key, value))

    _write_no_cache_headers(_Response())

    assert headers == [
        ("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"),
        ("Pragma", "no-cache"),
        ("Expires", "0"),
    ]
