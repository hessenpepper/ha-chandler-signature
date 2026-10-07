# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Config flow: pick (or discover) a valve, then enter its API token."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_TOKEN, DOMAIN
from .coordinator import CannotConnect, async_connect, async_disconnect
from .models import valve_type_name
from .protocol import MANUFACTURER_ID, parse_token
from .session import InvalidAuth

_LOGGER = logging.getLogger(__name__)

NAME_PREFIXES = ("C2_", "CS_")

TOKEN_SCHEMA = vol.Schema(
    {vol.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


def _is_chandler(info: BluetoothServiceInfoBleak) -> bool:
    return MANUFACTURER_ID in info.manufacturer_data or (info.name or "").startswith(NAME_PREFIXES)


class ChandlerSignatureConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup of a Chandler valve."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str = ""
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    # -- entry points ------------------------------------------------------------

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """A valve was seen over Bluetooth."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self._name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_token()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a valve that Home Assistant can currently see."""
        if user_input is not None:
            self._address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(self._address)
            self._abort_if_unique_id_configured()
            self._name = self._discovered[self._address].name or self._address
            return await self.async_step_token()

        configured = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, False):
            if info.address not in configured and _is_chandler(info):
                self._discovered[info.address] = info
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        choices = {
            address: f"{info.name or 'Chandler valve'} ({address})"
            for address, info in self._discovered.items()
        }
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(choices)})
        )

    # -- token -------------------------------------------------------------------

    async def _async_check(self, token_text: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
        """Connect with the token. Returns (valve data, token hex, error key)."""
        assert self._address is not None
        try:
            token = parse_token(token_text)
        except ValueError:
            return None, None, "invalid_token"
        try:
            client, session = await async_connect(self.hass, self._address, token)
        except InvalidAuth:
            return None, None, "invalid_auth"
        except CannotConnect as err:
            _LOGGER.debug("Cannot connect to %s: %s", self._address, err)
            return None, None, "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while checking the valve")
            return None, None, "unknown"
        state = dict(session.state)
        await async_disconnect(client, session)
        return state, token.hex(), None

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the API token (Legacy View app: enable the API on the valve)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            state, token_hex, error = await self._async_check(user_input[CONF_TOKEN])
            if error is None:
                title = f"{valve_type_name(state or {})} {self._name}".strip()
                return self.async_create_entry(
                    title=title,
                    data={CONF_ADDRESS: self._address, CONF_NAME: self._name, CONF_TOKEN: token_hex},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="token",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={"name": self._name},
        )

    # -- re-authentication -------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._address = entry_data[CONF_ADDRESS]
        self._name = entry_data.get(CONF_NAME, self._address)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            _state, token_hex, error = await self._async_check(user_input[CONF_TOKEN])
            if error is None:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_TOKEN: token_hex}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={"name": self._name},
        )
