"""Tests for browser renderer service helpers."""

from __future__ import annotations

import asyncio

import pytest

import ui.browser_service as browser_mod
from shared.config import UiConfig
from shared.events import Event, EventName
from shared.models import ComponentName
from ui.browser_service import _build_browser_command, _build_ws_accept, _write_no_cache_headers
from ui.browser_service import BrowserFaceUiService, _resolve_browser_executable


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


def test_resolve_browser_executable_prefers_configured_path_and_fallbacks(monkeypatch) -> None:
    assert _resolve_browser_executable("/opt/chrome") == "/opt/chrome"

    monkeypatch.setattr(browser_mod.sys, "platform", "linux")
    monkeypatch.setattr(browser_mod.shutil, "which", lambda name: "/usr/bin/" + name if name == "chromium" else None)
    assert _resolve_browser_executable("") == "/usr/bin/chromium"


def test_static_http_server_start_stop_and_handler_factory(monkeypatch, tmp_path) -> None:
    class _FakeHTTPServer:
        def __init__(self, address, handler):  # type: ignore[no-untyped-def]
            self.server_address = ("127.0.0.1", 4444)
            self.handler = handler
            self.shutdown_calls = 0
            self.close_calls = 0
            self.started = False

        def serve_forever(self) -> None:
            self.started = True

        def shutdown(self) -> None:
            self.shutdown_calls += 1

        def server_close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(browser_mod.http.server, "ThreadingHTTPServer", _FakeHTTPServer)
    server = browser_mod._StaticHttpServer(host="127.0.0.1", port=0, directory=tmp_path)
    assert server.start() == 4444
    handler = server._handler_factory()
    assert handler.__name__ == "_Handler"
    server.stop()


def test_websocket_peer_frame_writing_and_close() -> None:
    async def run() -> None:
        writes: list[bytes] = []

        class _Writer:
            def write(self, data: bytes) -> None:
                writes.append(bytes(data))

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                return None

            async def wait_closed(self) -> None:
                return None

        peer = browser_mod._WebSocketPeer(reader=asyncio.StreamReader(), writer=_Writer())
        await peer.send_json({"hello": "world"})
        await peer.send_text_bytes(b"abc")
        await peer.send_text_bytes(b"x" * 126)
        await peer.send_text_bytes(b"y" * 70000)
        await peer.close()
        assert any(chunk.startswith(b"\x81") for chunk in writes)
        assert any(chunk.startswith(b"\x88") for chunk in writes)

    asyncio.run(run())


def test_browser_service_publish_launch_sync_and_shutdown_helpers(monkeypatch) -> None:
    service = BrowserFaceUiService(
        config=UiConfig(
            backend="browser",
            browser_launch_mode="connect_only",
            sleep_command=("sleep",),
            wake_command=("wake",),
        )
    )

    class _PeerBag:
        def __init__(self, items: list[object] | None = None) -> None:
            self.items = list(items or [])

        def add(self, item: object) -> None:
            self.items.append(item)

        def discard(self, item: object) -> None:
            if item in self.items:
                self.items.remove(item)

        def __iter__(self):
            return iter(self.items)

    class _Peer:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail
            self.messages: list[dict[str, object]] = []
            self.closed = False

        async def send_json(self, payload: dict[str, object]) -> None:
            if self.fail:
                raise RuntimeError("send failed")
            self.messages.append(payload)

        async def close(self) -> None:
            self.closed = True

    good_peer = _Peer()
    bad_peer = _Peer(fail=True)
    service._peers = _PeerBag([good_peer, bad_peer])  # type: ignore[assignment]

    async def run() -> None:
        await service._publish_command(browser_mod.build_overlay_update_command(
            show_text_overlay=True,
            text="hi",
            content_mode="face",
            content_payload={},
        ))
        assert good_peer.messages
        assert bad_peer.closed is True

        service._last_renderer_state = browser_mod.build_renderer_state_command(
            scene=service.controller.state.scene,
            display_sleep_requested=False,
            controller_state=service.controller.state,
        )
        await service._publish_state_snapshot()
        service.controller.render_state("thinking", "thinking")
        await service._publish_state_snapshot()
        await service._publish_overlay_snapshot()

        recorded_commands: list[tuple[str, ...]] = []

        service._display_blanked = False
        async def fake_run_command(self, command: tuple[str, ...]) -> None:
            del self
            recorded_commands.append(command)

        monkeypatch.setattr(BrowserFaceUiService, "_run_command", fake_run_command)
        await service._sync_display_power("sleep", True)
        await service._sync_display_power("face", False)
        await service._wait_for_command_task(service._sleep_command_task)
        await service._wait_for_command_task(service._wake_command_task)
        assert recorded_commands == [("sleep",), ("wake",)]

        class _FakeProcess:
            def poll(self):
                return None

            def terminate(self) -> None:
                return None

            def wait(self, timeout=None):  # type: ignore[no-untyped-def]
                del timeout
                return None

        launch_calls: list[list[str]] = []
        popen_kwargs: dict[str, object] = {}
        monkeypatch.setattr(browser_mod, "_build_browser_command", lambda **kwargs: launch_calls.append(["launch"]) or ["/opt/chrome"])

        def fake_popen(command, **kwargs):  # type: ignore[no-untyped-def]
            del command
            popen_kwargs.update(kwargs)
            return _FakeProcess()

        monkeypatch.setattr(browser_mod.subprocess, "Popen", fake_popen)
        launch_service = BrowserFaceUiService(
            config=UiConfig(backend="browser", browser_launch_mode="windowed", browser_executable="/opt/chrome")
        )
        launch_service._launch_browser_if_needed()
        assert launch_calls == [["launch"]]
        assert popen_kwargs["stdout"] is browser_mod.subprocess.DEVNULL
        assert popen_kwargs["stderr"] is browser_mod.subprocess.DEVNULL
        assert popen_kwargs["start_new_session"] is True

        async def fake_start_server(*args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            class _Server:
                def close(self) -> None:
                    return None

                async def wait_closed(self) -> None:
                    return None

            return _Server()

        monkeypatch.setattr(browser_mod.asyncio, "start_server", fake_start_server)
        monkeypatch.setattr(browser_mod._StaticHttpServer, "start", lambda self: 1)
        shutdown_service = BrowserFaceUiService(
            config=UiConfig(backend="browser", browser_launch_mode="connect_only")
        )
        shutdown_service._ws_server = await browser_mod.asyncio.start_server(lambda r, w: None, host="127.0.0.1", port=0)
        shutdown_service._http_server = type("Http", (), {"stop": lambda self: None})()
        shutdown_service._browser_process = _FakeProcess()
        await shutdown_service._shutdown_partial_startup()
        assert shutdown_service._browser_process is None

    asyncio.run(run())


def test_browser_face_ui_service_lifecycle_and_event_routing(tmp_path, monkeypatch) -> None:
    config = UiConfig(
        backend="browser",
        browser_launch_mode="connect_only",
        browser_host="127.0.0.1",
        browser_http_port=0,
        browser_ws_port=0,
        browser_state_path=tmp_path / "override.json",
        show_text_overlay=True,
    )
    (tmp_path / "override.json").write_text(
        '{"baseVisual": {"eyeSize": 0.2}, "expressionModifiers": {"lookX": 0.1}}'
    )
    service = BrowserFaceUiService(config=config)

    class _FakeSocket:
        def getsockname(self):
            return ("127.0.0.1", 8766)

    class _FakeServer:
        sockets = [_FakeSocket()]

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    monkeypatch.setattr(browser_mod._StaticHttpServer, "start", lambda self: 8765)
    async def fake_start_server(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return _FakeServer()

    monkeypatch.setattr(browser_mod.asyncio, "start_server", fake_start_server)

    async def run() -> None:
        await service.start()
        assert service.runtime_url.startswith("http://127.0.0.1:")
        await service.show_text("Hello Oreo")
        await service.show_content("status", {"title": "Listening"})
        await service.clear_content()
        await service.handle_event(
            Event(name=EventName.TTS_PLAYBACK_STARTED, source=ComponentName.TTS, payload={})
        )
        await service.shutdown()

    asyncio.run(run())

    assert service._bridge_task is None or service._bridge_task.done()


def test_browser_face_ui_service_show_text_without_overlay_clears_display() -> None:
    service = BrowserFaceUiService(
        config=UiConfig(
            backend="browser",
            browser_launch_mode="connect_only",
            show_text_overlay=False,
        )
    )

    asyncio.run(service.show_text("Hidden"))
    assert service.controller.state.overlay_text is None


def test_browser_service_websocket_and_socket_helpers_cover_length_branches(monkeypatch, tmp_path) -> None:
    created_servers: list[object] = []

    class _FakeHTTPServer:
        def __init__(self, address, handler):  # type: ignore[no-untyped-def]
            self.server_address = ("127.0.0.1", 4321)
            self.handler = handler
            self.started = False
            self.shutdown_calls = 0
            self.close_calls = 0
            created_servers.append(self)

        def serve_forever(self) -> None:
            self.started = True

        def shutdown(self) -> None:
            self.shutdown_calls += 1

        def server_close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(browser_mod.http.server, "ThreadingHTTPServer", _FakeHTTPServer)
    server = browser_mod._StaticHttpServer(host="127.0.0.1", port=0, directory=tmp_path)
    assert server.start() == 4321
    server.stop()
    assert created_servers and created_servers[0].shutdown_calls == 1
    assert created_servers[0].close_calls == 1

    async def run() -> None:
        writer_bytes: list[bytes] = []

        class _Writer:
            def write(self, data: bytes) -> None:
                writer_bytes.append(bytes(data))

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                return None

            async def wait_closed(self) -> None:
                return None

        reader = asyncio.StreamReader()
        request = (
            "GET / HTTP/1.1\r\n"
            "Host: example\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "\r\n"
        ).encode("utf-8")

        def frame(opcode: int, payload: bytes = b"") -> bytes:
            mask = b"\x01\x02\x03\x04"
            masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            first = 0x80 | opcode
            length = len(payload)
            if length <= 125:
                second = 0x80 | length
                return bytes([first, second]) + mask + masked
            if length < 65536:
                return bytes([first, 0x80 | 126]) + length.to_bytes(2, "big") + mask + masked
            return bytes([first, 0x80 | 127]) + length.to_bytes(8, "big") + mask + masked

        reader.feed_data(
            request
            + frame(0x9)
            + frame(0x1, browser_mod.json.dumps({"hello": "world"}).encode("utf-8"))
            + frame(0x8)
        )
        reader.feed_eof()

        class _PeerBag:
            def __init__(self, items: list[object] | None = None) -> None:
                self.items: list[object] = list(items or [])

            def add(self, item: object) -> None:
                self.items.append(item)

            def discard(self, item: object) -> None:
                if item in self.items:
                    self.items.remove(item)

            def __iter__(self):
                return iter(self.items)

            def __eq__(self, other: object) -> bool:
                return isinstance(other, set) and not self.items and not other

        service = BrowserFaceUiService(
            config=UiConfig(backend="browser", browser_launch_mode="connect_only", show_text_overlay=True)
        )
        service._peers = _PeerBag()  # type: ignore[assignment]
        service._last_renderer_config = browser_mod.build_renderer_config_command(service.config)
        service._last_renderer_state = browser_mod.build_renderer_state_command(
            scene=service.controller.state.scene,
            display_sleep_requested=False,
            controller_state=service.controller.state,
        )
        service._last_overlay_update = browser_mod.build_overlay_update_command(
            show_text_overlay=True,
            text="hi",
            content_mode="face",
            content_payload={},
        )
        await service.start()
        await service.start()

        await service._handle_ws_connection(reader, _Writer())
        assert service._peers == set()
        assert any(data.startswith(b"HTTP/1.1 101") for data in writer_bytes)
        assert any(data[:2] == b"\x8a\x00" for data in writer_bytes)

        peer_writer = _Writer()
        peer = browser_mod._WebSocketPeer(reader=asyncio.StreamReader(), writer=peer_writer)
        await peer.send_json({"hello": "world"})
        await peer.send_text_bytes(b"abc")
        await peer.send_text_bytes(b"x" * 126)
        await peer.send_text_bytes(b"y" * 70000)
        await peer.close()
        assert peer_writer.write is not None

        browser_calls: list[list[str]] = []

        class _FakeProcess:
            def __init__(self) -> None:
                self.terminated = False
                self.waited = False

            def poll(self):
                return None

            def terminate(self) -> None:
                self.terminated = True

            def wait(self, timeout=None):  # type: ignore[no-untyped-def]
                del timeout
                self.waited = True

        launch_service = BrowserFaceUiService(
            config=UiConfig(
                backend="browser",
                browser_launch_mode="windowed",
                browser_executable="/opt/chrome",
                browser_extra_args=("--disable-gpu",),
            )
        )
        monkeypatch.setattr(
            browser_mod,
            "_build_browser_command",
            lambda **kwargs: browser_calls.append(["launch"]) or ["/opt/chrome"],
        )
        monkeypatch.setattr(browser_mod.subprocess, "Popen", lambda command: _FakeProcess())
        launch_service._launch_browser_if_needed()
        assert browser_calls == [["launch"]]
        async def fake_create_subprocess_exec(*args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            return _FakeProcess()

        monkeypatch.setattr(browser_mod.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        await launch_service._run_command(("echo", "hello"))

        sync_service = BrowserFaceUiService(
            config=UiConfig(
                backend="browser",
                browser_launch_mode="connect_only",
                sleep_command=("sleep",),
                wake_command=("wake",),
            )
        )
        sync_service._display_blanked = False
        recorded_commands: list[tuple[str, ...]] = []

        async def fake_run_command(command: tuple[str, ...]) -> None:
            recorded_commands.append(command)

        sync_service._run_command = fake_run_command  # type: ignore[assignment]
        await sync_service._sync_display_power("sleep", True)
        await sync_service._sync_display_power("face", False)
        await sync_service._wait_for_command_task(sync_service._sleep_command_task)
        await sync_service._wait_for_command_task(sync_service._wake_command_task)
        assert recorded_commands == [("sleep",), ("wake",)]

        publish_service = BrowserFaceUiService(
            config=UiConfig(backend="browser", browser_launch_mode="connect_only")
        )

        class _PublishPeer:
            def __init__(self, fail: bool = False) -> None:
                self.fail = fail
                self.messages: list[dict[str, object]] = []
                self.closed = False

            async def send_json(self, payload: dict[str, object]) -> None:
                if self.fail:
                    raise RuntimeError("send failed")
                self.messages.append(payload)

            async def close(self) -> None:
                self.closed = True

        good_peer = _PublishPeer()
        bad_peer = _PublishPeer(fail=True)
        publish_service._peers = _PeerBag([good_peer, bad_peer])  # type: ignore[assignment]
        command = browser_mod.build_renderer_state_command(
            scene=publish_service.controller.state.scene,
            display_sleep_requested=False,
            controller_state=publish_service.controller.state,
        )
        publish_service._last_renderer_state = command
        await publish_service._publish_state_snapshot()
        publish_service.controller.render_state("thinking", "thinking")
        await publish_service._publish_state_snapshot()
        await publish_service._publish_overlay_snapshot()
        await publish_service._publish_command(browser_mod.build_overlay_update_command(
            show_text_overlay=True,
            text="hi",
            content_mode="face",
            content_payload={},
        ))
        assert good_peer.messages
        assert bad_peer.closed is True

        shutdown_service = BrowserFaceUiService(
            config=UiConfig(backend="browser", browser_launch_mode="connect_only")
        )
        shutdown_service._ws_server = _FakeServer()
        shutdown_service._http_server = type("Http", (), {"stop": lambda self: None})()
        shutdown_service._browser_process = _FakeProcess()
        await shutdown_service._shutdown_partial_startup()
        assert shutdown_service._browser_process is None

        asyncio.run(run())
