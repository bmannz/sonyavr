"""Number entities for speaker levels and tone (JSON protocol receivers only).

Disabled by default: they are installation settings that are rarely changed.
Enable the ones you want under Settings > Devices & services > Entities.
"""

from __future__ import annotations

from homeassistant import config_entries, core
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .jsondevice import NUMBERS, NumberDef, SonyJsonDevice
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
    async_add_entities([SonyJsonNumber(device, definition) for definition in NUMBERS])


class SonyJsonNumber(SonyJsonFeatureEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_mode = NumberMode.BOX

    def __init__(self, device: SonyJsonDevice, definition: NumberDef) -> None:
        super().__init__(device, definition.feature, definition.name)
        self._def = definition
        self._attr_native_min_value = definition.minimum
        self._attr_native_max_value = definition.maximum
        self._attr_native_step = definition.step
        self._attr_native_unit_of_measurement = definition.unit

    @property
    def native_value(self) -> float | None:
        value = self._device.value(self._feature)
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        # Whole-number settings (bass/treble) are stored as integers.
        send = int(round(value)) if self._def.step >= 1 else float(value)
        await self._device.async_set_feature(self._feature, send)
