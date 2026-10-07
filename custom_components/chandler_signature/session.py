# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""One authenticated Signature API session with a valve.

The session only needs a Bleak-style client (``start_notify``, ``write_gatt_char``,
``stop_notify``), so it is exercised by both Home Assistant and the offline tools.

It is read-only: the only things ever written to the valve are the handshake, ACK/NAK
and keep-alive replies, and the documented reset (disconnect) command.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from .protocol import (
    ACK,
    ID_PACKET,
    READ_CHAR_UUID,
    RESET_COMMAND,
    WRITE_CHAR_UUID,
    PacketReader,
)

_LOGGER = logging.getLogger(__name__)


class SignatureError(Exception):
    """Base error for a Signature API session."""


class InvalidAuth(SignatureError):
    """The valve rejected the API token."""


class SessionClosed(SignatureError):
    """The session failed or the link dropped before it finished."""


class BleClient(Protocol):
    """The subset of a Bleak client the session uses."""

    async def start_notify(self, char: str, callback: Callable[[Any, bytearray], None]) -> None: ...
    async def stop_notify(self, char: str) -> None: ...
    async def write_gatt_char(self, char: str, data: bytes, response: bool = ...) -> None: ...


class SignatureSession:
    """Run the ID / token handshake and keep a merged copy of the valve's data."""

    def __init__(
        self,
        client: BleClient,
        token: bytes,
        on_data: Callable[[dict[str, Any]], None] | None = None,
        *,
        write_with_response: bool = True,
    ) -> None:
        if len(token) != 16:
            raise ValueError("token must be 16 bytes")
        self._client = client
        self._token = token
        self._on_data = on_data
        self._write_with_response = write_with_response
        self._reader = PacketReader()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None
        self._stage = "new"  # new -> id_sent -> token_sent
        self._authenticated = asyncio.Event()
        self._failed: Exception | None = None
        self._failure_event = asyncio.Event()
        self.state: dict[str, Any] = {}
        self.last_rx = time.monotonic()

    @property
    def authenticated(self) -> bool:
        return self._authenticated.is_set()

    @property
    def failure(self) -> Exception | None:
        return self._failed

    async def start(self) -> None:
        """Subscribe to notifications and send the ID packet."""
        self._writer = asyncio.create_task(self._write_loop())
        await self._client.start_notify(READ_CHAR_UUID, self._on_notify)
        self._stage = "id_sent"
        self._queue.put_nowait(ID_PACKET)

    async def wait_authenticated(self, timeout: float) -> None:
        """Wait until the valve reports it is authenticated, or raise."""
        done = asyncio.ensure_future(self._authenticated.wait())
        failed = asyncio.ensure_future(self._failure_event.wait())
        try:
            await asyncio.wait({done, failed}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            done.cancel()
            failed.cancel()
        if self._authenticated.is_set():
            return
        if self._failed is not None:
            raise self._failed
        raise TimeoutError("valve did not authenticate in time")

    async def close(self) -> None:
        """Ask the valve to disconnect gracefully and stop the writer."""
        with contextlib.suppress(Exception):
            await self._client.write_gatt_char(
                WRITE_CHAR_UUID, RESET_COMMAND, response=self._write_with_response
            )
        with contextlib.suppress(Exception):
            await self._client.stop_notify(READ_CHAR_UUID)
        if self._writer is not None:
            self._queue.put_nowait(None)
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._writer

    # -- internals ---------------------------------------------------------------

    def _fail(self, err: Exception) -> None:
        if self._failed is None:
            self._failed = err
            self._failure_event.set()

    def _on_notify(self, _char: Any, data: bytearray) -> None:
        self.last_rx = time.monotonic()
        events, replies = self._reader.feed(bytes(data))
        for reply in replies:
            self._queue.put_nowait(reply)
        for event in events:
            self._handle(event.kind, event.data)

    def _send_token(self) -> None:
        self._stage = "token_sent"
        self._queue.put_nowait(self._token)

    def _handle(self, kind: str, data: Any) -> None:
        if kind == "ack" and self._stage == "id_sent":
            # The valve acknowledged the ID packet: acknowledge back, then send the token.
            self._queue.put_nowait(bytes([ACK]))
            self._send_token()
        elif kind == "json":
            if self._stage == "id_sent":
                # Some firmware sends its data without a separate ACK first.
                self._send_token()
            self.state.update(data)
            if "as" in data:
                if data["as"] == 2:
                    self._authenticated.set()
                elif data["as"] == 1 and self._stage == "token_sent":
                    self._fail(InvalidAuth("the valve rejected the API token"))
            if self._on_data is not None and self._authenticated.is_set():
                self._on_data(self.state)

    async def _write_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                await self._client.write_gatt_char(
                    WRITE_CHAR_UUID, item, response=self._write_with_response
                )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - surfaced through wait_authenticated
            _LOGGER.debug("Write to valve failed: %s", err)
            self._fail(SessionClosed(f"write failed: {err}"))

