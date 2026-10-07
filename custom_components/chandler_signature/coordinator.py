# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Keeps one authenticated connection open to a valve and pushes its data to entities."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from bleak.backends.client import BaseBleakClient
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    AUTH_TIMEOUT,
    CONF_TOKEN,
    RETRY_MAX,
    RETRY_MIN,
    SILENCE_TIMEOUT,
    STARTUP_TIMEOUT,
)
from .protocol import WRITE_CHAR_UUID, parse_token
from .session import InvalidAuth, SessionClosed, SignatureError, SignatureSession

_LOGGER = logging.getLogger(__name__)


class CannotConnect(SignatureError):
    """The valve could not be reached or did not answer."""


async def async_connect(
    hass: HomeAssistant,
    address: str,
    token: bytes,
    *,
    on_data: Callable[[dict[str, Any]], None] | None = None,
    disconnected_callback: Callable[[Any], None] | None = None,
) -> tuple[Any, SignatureSession]:
    """Connect to a valve and authenticate. Raises InvalidAuth or CannotConnect."""

    def _device():
        return bluetooth.async_ble_device_from_address(hass, address, connectable=True)

    device = _device()
    if device is None:
        raise CannotConnect("valve is not in Bluetooth range")
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            device.name or address,
            disconnected_callback=disconnected_callback,
            max_attempts=3,
            ble_device_callback=lambda: _device() or device,
        )
    except (BleakError, TimeoutError, OSError) as err:
        raise CannotConnect(str(err)) from err

    write_with_response = True
    with contextlib.suppress(Exception):
        char = client.services.get_characteristic(WRITE_CHAR_UUID)
        if char is not None:
            write_with_response = "write" in char.properties

    session = SignatureSession(client, token, on_data, write_with_response=write_with_response)
    try:
        await session.start()
        await session.wait_authenticated(AUTH_TIMEOUT)
    except InvalidAuth:
        await async_disconnect(client, session)
        raise
    except (TimeoutError, SessionClosed, BleakError, OSError) as err:
        await async_disconnect(client, session)
        raise CannotConnect(str(err) or type(err).__name__) from err
    except BaseException:
        await async_disconnect(client, session)
        raise
    return client, session


async def async_disconnect(client: BaseBleakClient | Any, session: SignatureSession | None) -> None:
    """Ask the valve to hang up, then drop the link."""
    if session is not None:
        with contextlib.suppress(Exception):
            await session.close()
    with contextlib.suppress(Exception):
        await client.disconnect()


class ChandlerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the latest data pushed by one valve; reconnects when the link drops."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=entry.title, config_entry=entry)
        self.address: str = entry.data[CONF_ADDRESS]
        self._token = parse_token(entry.data[CONF_TOKEN])
        self.connected = False
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._first_data = asyncio.Event()
        self._disconnected = asyncio.Event()
        self._start_error: Exception | None = None

    # -- lifecycle ---------------------------------------------------------------

    async def async_start(self) -> None:
        """Start the connection loop and wait for the first data."""
        self._task = self.hass.async_create_background_task(
            self._run(), f"chandler_signature {self.address}"
        )
        waiter = asyncio.ensure_future(self._first_data.wait())
        try:
            await asyncio.wait({waiter}, timeout=STARTUP_TIMEOUT)
        finally:
            waiter.cancel()
        if self._first_data.is_set():
            return
        error = self._start_error
        await self.async_stop()
        if isinstance(error, InvalidAuth):
            raise error
        raise CannotConnect(str(error) if error else "no data received from the valve")

    async def async_stop(self) -> None:
        self._stopping = True
        self._disconnected.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    # -- connection loop ---------------------------------------------------------

    def _on_data(self, state: dict[str, Any]) -> None:
        self.async_set_updated_data(dict(state))
        self._first_data.set()

    def _on_disconnect(self, _client: Any) -> None:
        self._disconnected.set()

    async def _run(self) -> None:
        backoff = RETRY_MIN
        while not self._stopping:
            client = session = None
            self._disconnected.clear()
            try:
                client, session = await async_connect(
                    self.hass,
                    self.address,
                    self._token,
                    on_data=self._on_data,
                    disconnected_callback=self._on_disconnect,
                )
                self.connected = True
                self._start_error = None
                backoff = RETRY_MIN
                self.async_update_listeners()
                _LOGGER.debug("%s: connected and authenticated", self.address)
                await self._hold(session)
            except InvalidAuth as err:
                self._start_error = err
                _LOGGER.error("%s: the valve rejected the API token", self.address)
                if self._first_data.is_set():
                    self.config_entry.async_start_reauth(self.hass)
                return
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep trying
                self._start_error = err
                _LOGGER.debug("%s: connection problem: %s", self.address, err)
            finally:
                was_connected, self.connected = self.connected, False
                if client is not None:
                    await asyncio.shield(async_disconnect(client, session))
                if was_connected and not self._stopping:
                    self.async_update_listeners()
            if self._stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RETRY_MAX)

    async def _hold(self, session: SignatureSession) -> None:
        """Stay connected until the link drops or goes silent."""
        while not self._stopping:
            try:
                await asyncio.wait_for(self._disconnected.wait(), timeout=10)
                return
            except TimeoutError:
                pass
            if session.failure is not None:
                _LOGGER.debug("%s: session failed: %s", self.address, session.failure)
                return
            if time.monotonic() - session.last_rx > SILENCE_TIMEOUT:
                _LOGGER.debug("%s: no data from the valve, reconnecting", self.address)
                return
