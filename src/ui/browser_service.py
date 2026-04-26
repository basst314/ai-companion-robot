"""Browser-backed fullscreen face UI for Chromium kiosk and dev windows."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import http.server
import json
import logging
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.config import UiConfig
from shared.events import Event
from ui.browser_protocol import (
    BrowserCommand,
    build_overlay_update_command,
    build_renderer_config_command,
    build_renderer_state_command,
    load_browser_state_override,
    map_event_to_trigger_command,
)
from ui.face import FaceController

logger = logging.getLogger(__name__)

_WINDOWED_BROWSER_SIZE = "800,480"
_ASSET_ROOT = Path(__file__).resolve().parent / "browser_assets"
_RUNTIME_PAGE = "robot-face-runtime.html"
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _write_no_cache_headers(response: http.server.BaseHTTPRequestHandler) -> None:
    response.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    response.send_header("Pragma", "no-cache")
    response.send_header("Expires", "0")


@dataclass(slots=True)
class _StaticHttpServer:
    host: str
    port: int
    directory: Path
    _server: http.server.ThreadingHTTPServer | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> int:
        handler = self._handler_factory()
        self._server = http.server.ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="browser-ui-http",
            daemon=True,
        )
        self._thread.start()
        return int(self._server.server_address[1])

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _handler_factory(self):
        directory = str(self.directory)

        class _Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, directory=directory, **kwargs)

            def end_headers(self) -> None:
                _write_no_cache_headers(self)
                super().end_headers()

            def log_message(self, format: str, *args: object) -> None:
                logger.debug("browser-ui-http " + format, *args)

        return _Handler


@dataclass(slots=True, eq=False)
class _WebSocketPeer:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    async def send_json(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await self.send_text_bytes(data)

    async def send_text_bytes(self, data: bytes) -> None:
        header = bytearray()
        header.append(0x81)
        length = len(data)
        if length <= 125:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(127)
            header.extend(length.to_bytes(8, "big"))
        self.writer.write(header + data)
        await self.writer.drain()

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            self.writer.write(b"\x88\x00")
            await self.writer.drain()
        with contextlib.suppress(Exception):
            self.writer.close()
            await self.writer.wait_closed()


@dataclass(slots=True)
class BrowserFaceUiService:
    """Render the HTML face through a browser renderer bridge."""

    config: UiConfig
    controller: FaceController = field(init=False)
    _http_server: _StaticHttpServer | None = field(default=None, init=False, repr=False)
    _http_port: int = field(default=0, init=False, repr=False)
    _ws_server: asyncio.AbstractServer | None = field(default=None, init=False, repr=False)
    _ws_port: int = field(default=0, init=False, repr=False)
    _peers: set[_WebSocketPeer] = field(default_factory=set, init=False, repr=False)
    _bridge_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _sleep_command_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _wake_command_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _browser_process: subprocess.Popen[str] | None = field(default=None, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)
    _display_blanked: bool = field(default=False, init=False, repr=False)
    _content_mode: str = field(default="face", init=False, repr=False)
    _content_payload: dict[str, object] | None = field(default=None, init=False, repr=False)
    _last_renderer_state: BrowserCommand | None = field(default=None, init=False, repr=False)
    _last_overlay_update: BrowserCommand | None = field(default=None, init=False, repr=False)
    _last_renderer_config: BrowserCommand | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.controller = FaceController(
            idle_sleep_seconds=self.config.idle_sleep_seconds,
            sleeping_eyes_grace_seconds=self.config.sleeping_eyes_grace_seconds,
        )

    async def start(self) -> None:
        if self._bridge_task is not None:
            return
        self._stopped = False
        try:
            self._http_server = _StaticHttpServer(
                host=self.config.browser_host,
                port=self.config.browser_http_port,
                directory=_ASSET_ROOT,
            )
            self._http_port = self._http_server.start()
            self._ws_server = await asyncio.start_server(
                self._handle_ws_connection,
                host=self.config.browser_host,
                port=self.config.browser_ws_port,
            )
            sockets = self._ws_server.sockets or ()
            self._ws_port = int(sockets[0].getsockname()[1]) if sockets else self.config.browser_ws_port
            state_override = load_browser_state_override(self.config.browser_state_path)
            self._last_renderer_config = build_renderer_config_command(
                self.config,
                state_override=state_override,
            )
            self._last_overlay_update = build_overlay_update_command(
                show_text_overlay=self.config.show_text_overlay,
                text=self.controller.state.overlay_text,
                content_mode=self._content_mode,
                content_payload=self._content_payload,
            )
            self._last_renderer_state = build_renderer_state_command(
                scene=self.controller.state.scene,
                display_sleep_requested=False,
                controller_state=self.controller.state,
            )
            self._launch_browser_if_needed()
        except Exception:
            await self._shutdown_partial_startup()
            raise
        self._bridge_task = asyncio.create_task(self._bridge_loop())

    async def shutdown(self) -> None:
        self._stopped = True
        if self._bridge_task is not None:
            await self._bridge_task
            self._bridge_task = None
        await self._wait_for_command_task(self._sleep_command_task)
        await self._wait_for_command_task(self._wake_command_task)
        for peer in tuple(self._peers):
            await peer.close()
        self._peers.clear()
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None
        if self._http_server is not None:
            self._http_server.stop()
            self._http_server = None
        if self._browser_process is not None and self._browser_process.poll() is None:
            with contextlib.suppress(Exception):
                self._browser_process.terminate()
                self._browser_process.wait(timeout=5)
        self._browser_process = None
        self._http_port = 0
        self._ws_port = 0

    async def render_state(self, lifecycle: str, emotion: str, preview_text: str | None = None) -> None:
        self.controller.render_state(lifecycle, emotion, preview_text)
        await self._publish_state_snapshot()

    async def show_text(self, text: str) -> None:
        if self.config.show_text_overlay:
            self.controller.show_text(text)
        else:
            self.controller.clear_text()
        await self._publish_overlay_snapshot()

    async def show_content(self, mode: str, payload: dict[str, object] | None = None) -> None:
        self.controller.note_activity()
        self._content_mode = mode
        self._content_payload = payload or {}
        await self._publish_overlay_snapshot()

    async def clear_content(self) -> None:
        self.controller.note_activity()
        self._content_mode = "face"
        self._content_payload = None
        await self._publish_overlay_snapshot()

    async def handle_event(self, event: Event) -> None:
        self.controller.handle_event(event)
        await self._publish_state_snapshot()
        trigger_command = map_event_to_trigger_command(event)
        if trigger_command is not None:
            await self._publish_command(trigger_command)

    async def _bridge_loop(self) -> None:
        while not self._stopped:
            frame = self.controller.update()
            await self._sync_display_power(frame.scene, frame.display_sleep_requested)
            await self._publish_state_snapshot(scene=frame.scene, display_sleep_requested=frame.display_sleep_requested)
            await asyncio.sleep(0.15)

    async def _publish_state_snapshot(
        self,
        *,
        scene: str | None = None,
        display_sleep_requested: bool | None = None,
    ) -> None:
        command = build_renderer_state_command(
            scene=scene or self.controller.state.scene,
            display_sleep_requested=(
                self.controller.state.display_sleep_requested
                if display_sleep_requested is None
                else display_sleep_requested
            ),
            controller_state=self.controller.state,
        )
        if command == self._last_renderer_state:
            return
        self._last_renderer_state = command
        await self._publish_command(command)

    async def _publish_overlay_snapshot(self) -> None:
        command = build_overlay_update_command(
            show_text_overlay=self.config.show_text_overlay,
            text=self.controller.state.overlay_text,
            content_mode=self._content_mode,
            content_payload=self._content_payload,
        )
        self._last_overlay_update = command
        await self._publish_command(command)

    async def _publish_command(self, command: BrowserCommand) -> None:
        message = command.as_message()
        for peer in tuple(self._peers):
            try:
                await peer.send_json(message)
            except Exception:
                logger.exception("browser bridge peer send failed")
                self._peers.discard(peer)
                await peer.close()

    async def _sync_display_power(self, scene: str, display_sleep_requested: bool) -> None:
        del scene
        if display_sleep_requested and not self._display_blanked:
            self._display_blanked = True
            if self.config.sleep_command:
                self._sleep_command_task = asyncio.create_task(self._run_command(self.config.sleep_command))
        elif not display_sleep_requested and self._display_blanked:
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

    async def _shutdown_partial_startup(self) -> None:
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None
        if self._http_server is not None:
            self._http_server.stop()
            self._http_server = None
        if self._browser_process is not None and self._browser_process.poll() is None:
            with contextlib.suppress(Exception):
                self._browser_process.terminate()
                self._browser_process.wait(timeout=5)
        self._browser_process = None
        self._http_port = 0
        self._ws_port = 0

    async def _handle_ws_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request_line = await reader.readline()
        if not request_line:
            writer.close()
            await writer.wait_closed()
            return
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in {b"\r\n", b"\n", b""}:
                break
            key, _, value = line.decode("utf-8").partition(":")
            headers[key.strip().lower()] = value.strip()
        if headers.get("upgrade", "").lower() != "websocket":
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return
        accept_key = _build_ws_accept(headers.get("sec-websocket-key", ""))
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key}\r\n\r\n"
            ).encode("utf-8")
        )
        await writer.drain()

        peer = _WebSocketPeer(reader=reader, writer=writer)
        self._peers.add(peer)
        try:
            for command in (
                self._last_renderer_config,
                self._last_renderer_state,
                self._last_overlay_update,
            ):
                if command is not None:
                    await peer.send_json(command.as_message())
            await self._read_ws_frames(peer)
        finally:
            self._peers.discard(peer)
            await peer.close()

    async def _read_ws_frames(self, peer: _WebSocketPeer) -> None:
        try:
            while not self._stopped:
                try:
                    first = await peer.reader.readexactly(2)
                except asyncio.IncompleteReadError:
                    return
                opcode = first[0] & 0x0F
                masked = bool(first[1] & 0x80)
                length = first[1] & 0x7F
                if length == 126:
                    try:
                        length = int.from_bytes(await peer.reader.readexactly(2), "big")
                    except asyncio.IncompleteReadError:
                        return
                elif length == 127:
                    try:
                        length = int.from_bytes(await peer.reader.readexactly(8), "big")
                    except asyncio.IncompleteReadError:
                        return
                try:
                    mask = await peer.reader.readexactly(4) if masked else b""
                    payload = await peer.reader.readexactly(length) if length else b""
                except asyncio.IncompleteReadError:
                    return
                if masked and payload:
                    payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
                if opcode == 0x8:
                    return
                if opcode == 0x9:
                    peer.writer.write(b"\x8A\x00")
                    await peer.writer.drain()
                    continue
                if opcode == 0x1 and payload:
                    with contextlib.suppress(Exception):
                        message = json.loads(payload.decode("utf-8"))
                        logger.debug("browser bridge inbound message: %s", message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("browser websocket reader failed")

    def _launch_browser_if_needed(self) -> None:
        if self.config.browser_launch_mode == "connect_only":
            return
        command = _build_browser_command(
            config=self.config,
            url=self.runtime_url,
        )
        if command is None:
            raise RuntimeError(
                "browser UI backend requested launch mode "
                f"{self.config.browser_launch_mode!r} but no Chromium executable was found"
            )
        self._browser_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    @property
    def runtime_url(self) -> str:
        return (
            f"http://{self.config.browser_host}:{self._http_port}/{_RUNTIME_PAGE}"
            f"?ws={self._ws_port}"
        )


def _build_browser_command(*, config: UiConfig, url: str) -> list[str] | None:
    executable = _resolve_browser_executable(config.browser_executable)
    if executable is None:
        return None
    command = [executable]
    if config.browser_profile_dir is not None:
        command.append(f"--user-data-dir={config.browser_profile_dir}")
    if config.browser_launch_mode == "kiosk":
        command.extend(
            [
                "--kiosk",
                "--start-fullscreen",
                "--noerrdialogs",
                "--disable-session-crashed-bubble",
                "--disable-infobars",
            ]
        )
    else:
        command.extend(
            [
                f"--window-size={_WINDOWED_BROWSER_SIZE}",
                "--app=" + url,
            ]
        )
    command.extend(config.browser_extra_args)
    if config.browser_launch_mode == "kiosk":
        command.append(url)
    return command


def _resolve_browser_executable(configured: str) -> str | None:
    if configured.strip():
        return configured
    if sys.platform == "darwin":
        candidates = (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        )
        for candidate in candidates:
            if Path(candidate).exists():
                return candidate
    for name in ("chromium-browser", "chromium", "google-chrome", "chrome"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def _build_ws_accept(key: str) -> str:
    digest = hashlib.sha1((key + _WS_MAGIC).encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")
