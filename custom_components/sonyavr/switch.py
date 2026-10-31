"""Switch entities for on/off receiver settings (JSON protocol receivers only)."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries, core
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .jsondevice import SWITCHES, SonyJsonDevice, SwitchDef
from .jsonentity import SonyJsonFeatureEntity


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
) -> None:
    config = hass.data[DOMAIN][config_entry.entry_id]
    if not config.get("json"):
        return
    device: SonyJsonDevice = config["sonyavr"]
    async_add_entities([SonyJsonSwitch(device, definition) for definition in SWITCHES])


class SonyJsonSwitch(SonyJsonFeatureEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device: SonyJsonDevice, definition: SwitchDef) -> None:
        super().__init__(device, definition.feature, definition.name)
        self._attr_icon = definition.icon

    @property
    def is_on(self) -> bool | None:
        value = self._device.value(self._feature)
        return None if value is None else value == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._device.async_set_feature(self._feature, "on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._device.async_set_feature(self._feature, "off")
