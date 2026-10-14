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

try:
    from bleak_retry_connector import clear_cache
except ImportError:  # very old bleak-retry-connector: skip cache clearing
    clear_cache = None

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    AUTH_TIMEOUT,
    AUTO_SYNC_COOLDOWN,
    CONF_AUTO_SYNC,
    CONF_DRIFT_MINUTES,
    CONF_TOKEN,
    DEFAULT_AUTO_SYNC,
    DEFAULT_DRIFT_MINUTES,
    RETRY_MAX,
    RETRY_MIN,
    SILENCE_TIMEOUT,
    STARTUP_TIMEOUT,
)
from .models import clock_drift_minutes, seconds_to_next_minute, should_auto_sync
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

    # A service list cached while the adapter was misbehaving can be incomplete. If the
    # Chandler characteristics are missing, drop the cache so the next attempt rediscovers.
    char = None
    with contextlib.suppress(Exception):
        char = client.services.get_characteristic(WRITE_CHAR_UUID)
    if char is None:
        await async_disconnect(client, None)
        if clear_cache is not None:
            with contextlib.suppress(Exception):
                await clear_cache(address)
        raise CannotConnect("the valve's Bluetooth service list was incomplete; cleared the cache")
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
        self._session: SignatureSession | None = None
        self._client: Any = None
        self._clock_lock = asyncio.Lock()
        self._auto_sync_task: asyncio.Task[None] | None = None
        self._last_auto_sync: float | None = None  # time.monotonic() of the last attempt

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
        if self._auto_sync_task is not None:
            self._auto_sync_task.cancel()
            self._auto_sync_task = None
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    # -- the one write the integration offers ------------------------------------

    def _writable_session(self) -> tuple[SignatureSession, Any]:
        """Return (session, client) if the valve is connected and idle, else raise."""
        session, client = self._session, self._client
        if not self.connected or session is None or client is None:
            raise HomeAssistantError("The valve is not connected")
        data = self.data or {}
        if data.get("dlr") or data.get("drcp"):
            raise HomeAssistantError("The valve is regenerating; try again when it has finished")
        return session, client

    async def async_sync_clock(self) -> tuple[int, int]:
        """Set the valve's clock to Home Assistant's local time. Returns (hour, minute).

        Waits for the start of the next minute, then writes that minute with seconds set
        to 0, so the valve's seconds begin in step with real time. Only ``dh``, ``dm`` and
        ``ds`` are written. Refuses while the valve is regenerating.
        """
        if self._clock_lock.locked():
            raise HomeAssistantError("A clock sync is already waiting for the next minute")
        async with self._clock_lock:
            self._writable_session()
            for _ in range(3):
                now = dt_util.now()
                await asyncio.sleep(seconds_to_next_minute(now.second, now.microsecond) + 0.05)
                now = dt_util.now()
                if now.second < 5:
                    break  # we are just past the boundary
            else:
                raise HomeAssistantError("Could not line the write up with the minute change")
            session, client = self._writable_session()  # re-check after waiting
            max_packet = max(8, int(getattr(client, "mtu_size", 23)) - 3)
            data = self.data or {}
            _LOGGER.info(
                "%s: setting the valve clock to %02d:%02d:00 (it reported %s:%s)",
                self.address,
                now.hour,
                now.minute,
                data.get("dh"),
                data.get("dm"),
            )
            await session.send_json(
                {"dh": now.hour, "dm": now.minute, "ds": 0}, max_packet=max_packet
            )
            return now.hour, now.minute

    # -- automatic clock correction ----------------------------------------------

    def _maybe_auto_sync(self) -> None:
        """Start a clock sync if the valve has drifted past the configured threshold.

        Runs on every data push (the valve reports its time about once a minute). Never
        while regenerating, at most once per AUTO_SYNC_COOLDOWN, and only one at a time,
        so a valve that ignores the write cannot cause a loop.
        """
        options = self.config_entry.options
        if not options.get(CONF_AUTO_SYNC, DEFAULT_AUTO_SYNC):
            return
        if self._stopping or not self.connected or self._clock_lock.locked():
            return
        if self._auto_sync_task is not None and not self._auto_sync_task.done():
            return
        data = self.data or {}
        if data.get("dlr") or data.get("drcp"):
            return
        now = dt_util.now()
        drift = clock_drift_minutes(data.get("dh"), data.get("dm"), now.hour, now.minute)
        since = (
            None if self._last_auto_sync is None else time.monotonic() - self._last_auto_sync
        )
        threshold = int(options.get(CONF_DRIFT_MINUTES, DEFAULT_DRIFT_MINUTES))
        if not should_auto_sync(drift, threshold, since, AUTO_SYNC_COOLDOWN):
            return
        self._last_auto_sync = time.monotonic()
        _LOGGER.info(
            "%s: valve clock is %+d min off (valve %s:%s, real %02d:%02d); syncing",
            self.address,
            drift,
            data.get("dh"),
            data.get("dm"),
            now.hour,
            now.minute,
        )
        self._auto_sync_task = self.hass.async_create_background_task(
            self._auto_sync(), f"chandler_signature auto-sync {self.address}"
        )

    async def _auto_sync(self) -> None:
        try:
            hour, minute = await self.async_sync_clock()
        except HomeAssistantError as err:
            _LOGGER.warning("%s: automatic clock sync did not run: %s", self.address, err)
        except Exception:  # noqa: BLE001 - never let this kill the connection loop
            _LOGGER.exception("%s: automatic clock sync failed", self.address)
        else:
            _LOGGER.info("%s: valve clock set to %02d:%02d", self.address, hour, minute)

    # -- connection loop ---------------------------------------------------------

    def _on_data(self, state: dict[str, Any]) -> None:
        self.async_set_updated_data(dict(state))
        self._first_data.set()
        self._maybe_auto_sync()

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
                self._session, self._client = session, client
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
                self._session = self._client = None
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
