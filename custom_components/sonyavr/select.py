"""Select entities for receiver settings (JSON protocol receivers only)."""

from __future__ import annotations

from homeassistant import config_entries, core
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .jsonclient import CONNECTION
from .jsondevice import SELECTS, SelectDef, SonyJsonDevice
from .jsonentity import SonyJsonEntity, SonyJsonFeatureEntity


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
) -> None:
    config = hass.data[DOMAIN][config_entry.entry_id]
    if not config.get("json"):
        return
    device: SonyJsonDevice = config["sonyavr"]
    async_add_entities(
        [SonyJsonSelect(device, definition) for definition in SELECTS]
        + [SonyJsonTunerStation(device)]
    )


class SonyJsonSelect(SonyJsonFeatureEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, device: SonyJsonDevice, definition: SelectDef) -> None:
        super().__init__(device, definition.feature, definition.name)
        self._def = definition
        labels = definition.labels or {}
        self._label = {raw: labels.get(raw, raw) for raw in definition.options}
        self._raw = {label: raw for raw, label in self._label.items()}
        self._attr_options = list(self._label.values())
        self._attr_icon = definition.icon

    @property
    def current_option(self) -> str | None:
        value = self._device.value(self._feature)
        if value is None:
            return None
        return self._label.get(str(value))

    async def async_select_option(self, option: str) -> None:
        raw = self._raw[option]
        value = int(raw) if self._def.numeric else raw
        await self._device.async_set_feature(self._feature, value)


class SonyJsonTunerStation(SonyJsonEntity, SelectEntity):
    """Pick a stored FM/AM preset; switches the main zone to the tuner if needed."""

    _attr_name = "Tuner station"
    _attr_icon = "mdi:radio"
    _TRIGGERS = ("main.input", "tuner.preset", "tuner.band")

    def __init__(self, device: SonyJsonDevice) -> None:
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_base}_tuner_station"

    @callback
    def _handle_change(self, changed: set[str]) -> None:
        if (
            CONNECTION in changed
            or changed.intersection(self._TRIGGERS)
            or any("preset" in feature for feature in changed)
        ):
            self.async_write_ha_state()

    @property
    def options(self) -> list[str]:
        return list(self._device.preset_options())

    @property
    def current_option(self) -> str | None:
        return self._device.current_preset_label()

    async def async_select_option(self, option: str) -> None:
        target = self._device.preset_options().get(option)
        if target is None:
            raise HomeAssistantError(f"Unknown station {option!r}")
        if not await self._device.async_tune_preset(*target):
            raise HomeAssistantError(f"The receiver would not tune {option}")
