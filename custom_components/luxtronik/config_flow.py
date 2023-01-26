"""Config flow to configure the Luxtronik heatpump controller integration."""
# region Imports
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.dhcp import DhcpServiceInfo
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_entry_flow, selector

from .const import (
    CONF_CONTROL_MODE_HOME_ASSISTANT,
    CONF_HA_SENSOR_INDOOR_TEMPERATURE,
    CONF_HA_SENSOR_PREFIX,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
    LOGGER,
)
from .coordinator import LuxtronikCoordinator
from .lux_helper import discover

# endregion Imports

PORT_SELECTOR = vol.All(
    selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1, step=1, max=65535, mode=selector.NumberSelectorMode.BOX
        )
    ),
    vol.Coerce(int),
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): PORT_SELECTOR,
    }
)


def _get_options_schema(options, default_sensor_indoor_temperature: str) -> vol.Schema:
    """Build and return the options schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_HA_SENSOR_INDOOR_TEMPERATURE,
                default=default_sensor_indoor_temperature,
                description={
                    "suggested_value": None
                    if options is None
                    else options.get(CONF_HA_SENSOR_INDOOR_TEMPERATURE)
                },
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=Platform.SENSOR)
            ),
            # vol.Optional(CONF_CONTROL_MODE_HOME_ASSISTANT, default=False): bool,
            # vol.Required(
            #     CONF_HA_SENSOR_PREFIX,
            #     default=f"luxtronik_{unique_id}",
            #     description={
            #         "suggested_value": None
            #         if options is None
            #         else options.get(CONF_HA_SENSOR_PREFIX)
            #     },
            # ): str,
        }
    )


# CONFIG_SCHEMA = STEP_OPTIONS_DATA_SCHEMA

async def _async_has_devices(hass: HomeAssistant) -> bool:
    """Return if there are devices that can be discovered."""
    # Check if there are any devices that can be discovered in the network.
    first_device = await hass.async_add_executor_job(discover)
    return first_device is not None


class LuxtronikFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Luxtronik heatpump controller config flow."""

    VERSION = 3
    _hassio_discovery = None
    _discovery_host = None
    _discovery_port = None
    _discovery_schema = None

    _sensor_prefix = DOMAIN
    _title = "Luxtronik"

    def _get_schema(self):
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._discovery_host): str,
                vol.Required(CONF_PORT, default=self._discovery_port): int,
                vol.Optional(CONF_CONTROL_MODE_HOME_ASSISTANT, default=False): bool,
                vol.Optional(
                    CONF_HA_SENSOR_INDOOR_TEMPERATURE,
                    default=f"sensor.{self._sensor_prefix}_room_temperature",
                ): str,
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initiated by the user."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )
        return await self.async_step_options(user_input)

    async def _async_migrate_data_from_custom_component_luxtronik2(self):
        """
        Migrate custom_components/luxtronik2 to components/luxtronik.

            - If serial number matches
            1. Set CONF_HA_SENSOR_PREFIX = "luxtronik2"
            2. Disable custom_components/luxtronik2
        """
        # Check if custom_component_luxtronik2 exists:
        for legacy_entry in self.hass.config_entries.async_entries("luxtronik2"):
            if CONF_HOST not in legacy_entry.data or CONF_PORT not in legacy_entry.data:
                continue
            try:
                # Try to connect and lookup serial number:
                coord_legacy = LuxtronikCoordinator.connect(self.hass, legacy_entry)
                if self.context["unique_id"] == coord_legacy.unique_id:
                    # Match Found! --> Migrate
                    # How to use .INTEGRATION or other instead of .USER?
                    legacy_entry.disabled_by = config_entries.ConfigEntryDisabler.USER
                    self.hass.config_entries.async_update_entry(legacy_entry)
                    await self.hass.config_entries.async_reload(legacy_entry.entry_id)
                    self.context["data"][CONF_HA_SENSOR_PREFIX] = "luxtronik2"
                    if (
                        hasattr(legacy_entry, "data")
                        and CONF_HA_SENSOR_INDOOR_TEMPERATURE in legacy_entry.data
                    ):
                        self.context["data"][
                            CONF_HA_SENSOR_INDOOR_TEMPERATURE
                        ] = legacy_entry.data[CONF_HA_SENSOR_INDOOR_TEMPERATURE]
                    return
            except Exception:  # pylint: disable=broad-except
                continue

    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Return config entry title."""
        return self._title

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow option step."""

        if "data" not in self.context:
            self.context["data"] = {}
        self.context["data"] |= user_input
        data = self.context["data"]

        try:
            coordinator = LuxtronikCoordinator.connect(self.hass, data)
        except Exception:  # pylint: disable=broad-except
            return self.async_abort(reason="cannot_connect")

        self._title = (
            title
        ) = f"{coordinator.manufacturer} {coordinator.model} {coordinator.serial_number}"
        name = f"{title} ({data[CONF_HOST]}:{data[CONF_PORT]})"

        await self.async_set_unique_id(coordinator.unique_id)
        self._abort_if_unique_id_configured()

        self.context["data"][
            CONF_HA_SENSOR_PREFIX
        ] = f"luxtronik_{coordinator.unique_id}"
        self.context["data"][
            CONF_HA_SENSOR_INDOOR_TEMPERATURE
        ] = f"sensor.{self._sensor_prefix}_room_temperature"
        await self._async_migrate_data_from_custom_component_luxtronik2()
        if user_input is not None and CONF_HA_SENSOR_INDOOR_TEMPERATURE in user_input:
            return self.async_create_entry(title=title, data=data)
        return self.async_show_form(
            step_id="options",
            data_schema=_get_options_schema(
                None, self.context["data"][CONF_HA_SENSOR_INDOOR_TEMPERATURE]
            ),
            description_placeholders={"name": name},
        )

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> FlowResult:
        """Prepare configuration for a DHCP discovered Luxtronik heatpump."""
        LOGGER.info(
            "Found device with hostname '%s' IP '%s'",
            discovery_info.hostname,
            discovery_info.ip,
        )
        # Validate dhcp result with socket broadcast:
        broadcast_discover_ip, broadcast_discover_port = discover()[0]
        if broadcast_discover_ip != discovery_info.ip:
            return self.async_abort(reason="no_devices_found")
        config = dict[str, Any]()
        config[CONF_HOST] = broadcast_discover_ip
        config[CONF_PORT] = broadcast_discover_port
        try:
            coordinator = LuxtronikCoordinator.connect(self.hass, config)
        except Exception:  # pylint: disable=broad-except
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(coordinator.unique_id)
        self._abort_if_unique_id_configured()

        self._discovery_host = discovery_info.ip
        self._discovery_port = (
            DEFAULT_PORT if broadcast_discover_port is None else broadcast_discover_port
        )
        self._discovery_schema = self._get_schema()
        return await self.async_step_user()

    async def _show_setup_form(
        self, errors: dict[str, str] | None = None
    ) -> FlowResult:
        """Show the setup form to the user."""
        return self.async_show_form(
            step_id="user",
            data_schema=self._get_schema(),
            errors=errors or {},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get default options flow."""
        return LuxtronikOptionsFlowHandler(config_entry)


class LuxtronikOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle a Luxtronik options flow."""

    _sensor_prefix = DOMAIN

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    def _get_value(self, key: str, default=None):
        """Return a value from Luxtronik."""
        return self.config_entry.options.get(
            key, self.config_entry.data.get(key, default)
        )

    # def _get_options_schema(self):
    #     """Return a schema for Luxtronik configuration options."""
    #     return vol.Schema(
    #         {
    #             vol.Optional(
    #                 CONF_CONTROL_MODE_HOME_ASSISTANT,
    #                 default=self._get_value(CONF_CONTROL_MODE_HOME_ASSISTANT, False),
    #             ): bool,
    #             vol.Optional(
    #                 CONF_HA_SENSOR_INDOOR_TEMPERATURE,
    #                 default=self._get_value(
    #                     CONF_HA_SENSOR_INDOOR_TEMPERATURE,
    #                     f"sensor.{self._sensor_prefix}_room_temperature",
    #                 ),
    #             ): str,
    #         }
    #     )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle a flow initialized by the user."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        coordinator = LuxtronikCoordinator.connect(self.hass, self.config_entry)
        title = f"{coordinator.manufacturer} {coordinator.model} {coordinator.serial_number}"
        name = f"{title} ({self.config_entry.data[CONF_HOST]}:{self.config_entry.data[CONF_PORT]})"
        return self.async_show_form(
            step_id="user",
            data_schema=_get_options_schema(None, coordinator.serial_number),
            description_placeholders={"name": name},
        )


config_entry_flow.register_discovery_flow(DOMAIN, "Luxtronik", _async_has_devices)
