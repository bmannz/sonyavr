"""Shared base entity for receivers using the JSON protocol."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .jsonclient import CONNECTION
from .jsondevice import SonyJsonDevice


def _mac(value: object) -> str | None:
    """The receiver reports octets without leading zeros ("dc:79:0")."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 6:
        return None
    return ":".join(part.zfill(2) for part in parts).lower()


class SonyJsonEntity(Entity):
    """Entity bound to a SonyJsonDevice; updated by receiver push notifications."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Features whose change should refresh this entity. Empty = any change.
    _features: tuple[str, ...] = ()

    def __init__(self, device: SonyJsonDevice) -> None:
        self._device = device

    @property
    def device_info(self) -> DeviceInfo:
        info = DeviceInfo(
            identifiers={(DOMAIN, self._device.unique_base)},
            name=self._device.name,
            manufacturer="Sony",
            model=self._device.model,
        )
        version = self._device.value("system.version")
        if version is not None:
            info["sw_version"] = str(version)
        mac = _mac(self._device.value("network.macaddress"))
        if mac:
            info["connections"] = {(CONNECTION_NETWORK_MAC, mac)}
        return info

    @property
    def available(self) -> bool:
        return self._device.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._device.client.add_listener(self._handle_change))

    @callback
    def _handle_change(self, changed: set[str]) -> None:
        if (
            CONNECTION in changed
            or not self._features
            or changed.intersection(self._features)
        ):
            self.async_write_ha_state()


class SonyJsonFeatureEntity(SonyJsonEntity):
    """Entity backed by a single receiver feature that may be NAK (unavailable)."""

    def __init__(self, device: SonyJsonDevice, feature: str, name: str) -> None:
        super().__init__(device)
        self._feature = feature
        self._features = (feature,)
        self._attr_name = name
        self._attr_unique_id = f"{device.unique_base}_{feature.replace('.', '_')}"

    @property
    def available(self) -> bool:
        return self._device.feature_available(self._feature)
