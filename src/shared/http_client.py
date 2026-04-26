"""Small persistent HTTP helpers for low-latency local and cloud calls."""

from __future__ import annotations

import asyncio
import http.client
import json
import ssl
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib import parse


@dataclass(slots=True, frozen=True)
class HttpResponse:
    """HTTP response payload plus timing metadata."""

    status: int
    headers: Mapping[str, str]
    body: bytes
    first_byte_at: datetime | None = None
    finished_at: datetime | None = None

    def json(self) -> dict[str, Any]:
        payload = json.loads(self.body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("expected a JSON object response")
        return payload

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class AsyncPersistentHttpClient:
    """Tiny async wrapper around one persistent HTTP/1.1 connection."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 20.0,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        parsed = parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")
        self._parsed = parsed
        self._timeout_seconds = timeout_seconds
        self._default_headers = dict(default_headers or {})
        self._lock = asyncio.Lock()
        self._connection: http.client.HTTPConnection | http.client.HTTPSConnection | None = None

    async def get(self, path: str = "", *, headers: Mapping[str, str] | None = None) -> HttpResponse:
        return await self.request("GET", path=path, headers=headers)

    async def post(
        self,
        path: str = "",
        *,
        body: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        return await self.request("POST", path=path, headers=headers, body=body)

    async def request(
        self,
        method: str,
        *,
        path: str = "",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        async with self._lock:
            return await asyncio.to_thread(
                self._request_with_reconnect,
                method,
                path,
                dict(headers or {}),
                body,
            )

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._close_connection)

    def _request_with_reconnect(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(2):
            connection = self._ensure_connection(reset=attempt > 0)
            request_path = self._request_path(path)
            request_headers = dict(self._default_headers)
            request_headers.update(headers)
            if body is not None and "Content-Length" not in request_headers:
                request_headers["Content-Length"] = str(len(body))
            if "Connection" not in request_headers:
                request_headers["Connection"] = "keep-alive"
            try:
                connection.request(method.upper(), request_path, body=body, headers=request_headers)
                response = connection.getresponse()
                first_byte_at = datetime.now(UTC)
                response_body = response.read()
                finished_at = datetime.now(UTC)
                return HttpResponse(
                    status=response.status,
                    headers={key: value for key, value in response.getheaders()},
                    body=response_body,
                    first_byte_at=first_byte_at,
                    finished_at=finished_at,
                )
            except Exception as exc:
                last_error = exc
                self._close_connection()
        assert last_error is not None
        raise last_error

    def _ensure_connection(self, *, reset: bool = False):
        if reset:
            self._close_connection()
        if self._connection is not None:
            return self._connection
        host = self._parsed.hostname
        if not host:
            raise ValueError(f"invalid base_url host: {self._parsed.geturl()!r}")
        port = self._parsed.port
        if self._parsed.scheme == "https":
            context = ssl.create_default_context()
            self._connection = http.client.HTTPSConnection(
                host,
                port=port,
                timeout=self._timeout_seconds,
                context=context,
            )
        else:
            self._connection = http.client.HTTPConnection(
                host,
                port=port,
                timeout=self._timeout_seconds,
            )
        return self._connection

    def _close_connection(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.close()
        finally:
            self._connection = None

    def _request_path(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            parsed = parse.urlparse(path)
            suffix = parsed.path or "/"
            if parsed.query:
                suffix = f"{suffix}?{parsed.query}"
            return suffix
        base_path = self._parsed.path or "/"
        if not path:
            return base_path
        if path.startswith("/"):
            return path
        prefix = base_path.rstrip("/")
        return f"{prefix}/{path}"


def encode_multipart_form_data(
    fields: Mapping[str, str],
    *,
    file_field: str,
    file_name: str,
    file_content: bytes,
    file_content_type: str,
) -> tuple[bytes, str]:
    """Serialize one small multipart/form-data body."""

    boundary = f"----ai-companion-{uuid.uuid4().hex}"
    lines: list[bytes] = []
    for name, value in fields.items():
        lines.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    lines.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {file_content_type}\r\n\r\n".encode("utf-8"),
            file_content,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(lines), f"multipart/form-data; boundary={boundary}"
