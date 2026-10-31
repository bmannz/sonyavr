"""Sensors for receivers using the JSON protocol."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .jsondevice import SonyJsonDevice
from .jsonentity import SonyJsonEntity, SonyJsonFeatureEntity

NO_PRESET = 255


def build_json_sensors(device: SonyJsonDevice) -> list[SensorEntity]:
    return [
        SonyJsonVolumeSensor(device),
        SonyJsonTunerFrequency(device),
        SonyJsonTunerPreset(device),
        SonyJsonDiagnostic(device, "system.version", "Firmware version"),
        SonyJsonDiagnostic(device, "network.ipaddress", "IP address"),
        SonyJsonDiagnostic(device, "speaker.pattern", "Speaker layout"),
    ]


class SonyJsonVolumeSensor(SonyJsonEntity, SensorEntity):
    """Main zone volume in dB (kept compatible with the original sensor)."""

    _attr_name = "Volume"
    _attr_device_class = SensorDeviceClass.SOUND_PRESSURE
    _attr_native_unit_of_measurement = "dB"
    _features = ("main.volumedb", "main.mute")

    def __init__(self, device: SonyJsonDevice) -> None:
        super().__init__(device)
        self._attr_unique_id = device.unique_base

    @property
    def icon(self) -> str:
        return "mdi:volume-off" if self._device.muted() else "mdi:volume-high"

    @property
    def native_value(self) -> float | None:
        return self._device.volume_db()


class SonyJsonTunerFrequency(SonyJsonFeatureEntity, SensorEntity):
    """Current tuner frequency: MHz on FM, kHz on AM (the receiver reports 8825 for 88.25 MHz)."""

    _attr_device_class = SensorDeviceClass.FREQUENCY
    _attr_icon = "mdi:radio"

    def __init__(self, device: SonyJsonDevice) -> None:
        super().__init__(device, "tuner.frequency", "Tuner frequency")
        self._features = ("tuner.frequency", "tuner.band")

    @property
    def _band(self) -> str | None:
        band = self._device.value("tuner.band")
        return band if isinstance(band, str) else None

    @property
    def native_unit_of_measurement(self) -> str:
        return "kHz" if self._band == "am" else "MHz"

    @property
    def native_value(self) -> float | None:
        value = self._device.value("tuner.frequency")
        if not isinstance(value, (int, float)):
            return None
        return value if self._band == "am" else round(value / 100, 2)


class SonyJsonTunerPreset(SonyJsonFeatureEntity, SensorEntity):
    _attr_icon = "mdi:radio-tower"

    def __init__(self, device: SonyJsonDevice) -> None:
        super().__init__(device, "tuner.preset", "Tuner preset")

    @property
    def native_value(self) -> int | None:
        value = self._device.value("tuner.preset")
        if not isinstance(value, int) or value == NO_PRESET:
            return None
        return value


class SonyJsonDiagnostic(SonyJsonFeatureEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: SonyJsonDevice, feature: str, name: str) -> None:
        super().__init__(device, feature, name)

    @property
    def native_value(self) -> str | None:
        value = self._device.value(self._feature)
        return None if value is None else str(value)
