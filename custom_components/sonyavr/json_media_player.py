"""Media player entities for receivers using the JSON protocol."""

from __future__ import annotations

import logging
from typing import Any

import probatio
from homeassistant import config_entries, core
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform

from .const import CONF_ZONES, SERVICE_SEND_COMMAND, SERVICE_UPDATE_STATE
from .jsondevice import MAIN, SonyJsonDevice
from .jsonentity import SonyJsonEntity

_LOGGER = logging.getLogger(__name__)

SUPPORT_MAIN = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.SELECT_SOUND_MODE
)
SUPPORT_ZONE = SUPPORT_MAIN & ~MediaPlayerEntityFeature.SELECT_SOUND_MODE


async def async_setup_json_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
    config: dict[str, Any],
) -> None:
    device: SonyJsonDevice = config["sonyavr"]

    zones = sorted(config_entry.options.get(CONF_ZONES, []))
    config["zones"] = zones
    entities: list[MediaPlayerEntity] = [SonyJsonMediaPlayer(device)]
    entities += [
        SonyJsonZonePlayer(device, f"zone{zone.split()[-1]}") for zone in zones
    ]
    async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SEND_COMMAND,
        {
            probatio.Required("Command"): cv.string,
            probatio.Optional("Value"): cv.string,
        },
        "send_command",
    )
    platform.async_register_entity_service(SERVICE_UPDATE_STATE, {}, "update_status")


class _PlayerCommands:
    """Services shared by the main and zone players."""

    _device: SonyJsonDevice
    _zone: str

    async def update_status(self) -> None:
        await self._device.async_refresh_all()

    async def send_command(self, Command: str, Value: str | None = None) -> None:  # noqa: N803
        device, zone = self._device, self._zone
        match Command:
            case "Power On":
                await device.async_set_power(True, zone)
            case "Power Off":
                await device.async_set_power(False, zone)
            case "Mute":
                await device.async_set_mute(True, zone)
            case "UnMute":
                await device.async_set_mute(False, zone)
            case "Volume Up":
                await device.async_volume_step(True, zone)
            case "Volume Down":
                await device.async_volume_step(False, zone)
            case "Source Up":
                await device.async_source_step(True, zone)
            case "Source Down":
                await device.async_source_step(False, zone)
            case "Set Volume":
                if Value is None:
                    raise HomeAssistantError("Set Volume needs a value in dB, e.g. -30")
                await device.async_set_volume_db(float(Value), zone)
            case "Set Source":
                if Value is None:
                    raise HomeAssistantError("Set Source needs a source name")
                await device.async_set_source(Value, zone)
            case "Set Sound Field":
                if Value is None:
                    raise HomeAssistantError("Set Sound Field needs a sound field")
                await device.async_set_sound_mode(Value)
            case "Set Feature":
                # Value: "feature=value", e.g. "system.dimmer=60" or "audio.dll=auto1"
                feature, sep, raw = (Value or "").partition("=")
                if not sep or not feature.strip():
                    raise HomeAssistantError('Set Feature needs a value like "audio.dll=auto1"')
                ok = await device.async_set_feature(
                    feature.strip(), device.parse_value(raw.strip())
                )
                if not ok:
                    raise HomeAssistantError(f"The receiver rejected {Value}")
            case "Get Feature":
                feature = (Value or "").strip()
                if not feature:
                    raise HomeAssistantError("Get Feature needs a feature name")
                _LOGGER.info("%s = %r", feature, await device.async_get_feature(feature))
            case _:
                raise HomeAssistantError(f"Unsupported command for this receiver: {Command}")


class SonyJsonMediaPlayer(_PlayerCommands, SonyJsonEntity, MediaPlayerEntity):
    """Main zone."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = SUPPORT_MAIN
    _zone = MAIN

    def __init__(self, device: SonyJsonDevice) -> None:
        super().__init__(device)
        self._attr_unique_id = device.unique_base

    @property
    def icon(self) -> str:
        return "mdi:audio-video" if self._device.power() else "mdi:audio-video-off"

    @property
    def state(self) -> MediaPlayerState | None:
        power = self._device.power()
        if power is None:
            return None
        return MediaPlayerState.ON if power else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        return self._device.volume_level()

    @property
    def is_volume_muted(self) -> bool | None:
        return self._device.muted()

    @property
    def source_list(self) -> list[str]:
        return list(self._device.source_list())

    @property
    def source(self) -> str | None:
        return self._device.source()

    @property
    def sound_mode_list(self) -> list[str]:
        return list(self._device.sound_mode_list())

    @property
    def sound_mode(self) -> str | None:
        return self._device.sound_mode()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"volume": self._device.volume_db()}

    async def async_turn_on(self) -> None:
        await self._device.async_set_power(True)

    async def async_turn_off(self) -> None:
        await self._device.async_set_power(False)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._device.async_set_volume_level(volume)

    async def async_volume_up(self) -> None:
        await self._device.async_volume_step(True)

    async def async_volume_down(self) -> None:
        await self._device.async_volume_step(False)

    async def async_mute_volume(self, mute: bool) -> None:
        await self._device.async_set_mute(mute)

    async def async_select_source(self, source: str) -> None:
        await self._device.async_set_source(source)

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        await self._device.async_set_sound_mode(sound_mode)


class SonyJsonZonePlayer(_PlayerCommands, SonyJsonEntity, MediaPlayerEntity):
    """Zone 2 or Zone 3."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = SUPPORT_ZONE

    def __init__(self, device: SonyJsonDevice, zone: str) -> None:
        super().__init__(device)
        self._zone = zone
        number = zone[-1]
        self._attr_name = f"Zone {number}"
        self._attr_unique_id = f"{device.unique_base}_{zone}"

    @property
    def icon(self) -> str:
        return "mdi:speaker-multiple" if self._device.power(self._zone) else "mdi:speaker-off"

    @property
    def state(self) -> MediaPlayerState | None:
        power = self._device.power(self._zone)
        if power is None:
            return None
        return MediaPlayerState.ON if power else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        return self._device.volume_level(self._zone)

    @property
    def is_volume_muted(self) -> bool | None:
        return self._device.muted(self._zone)

    @property
    def source_list(self) -> list[str]:
        return list(self._device.source_list())

    @property
    def source(self) -> str | None:
        return self._device.source(self._zone)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"volume": self._device.volume_db(self._zone)}

    async def async_turn_on(self) -> None:
        await self._device.async_set_power(True, self._zone)

    async def async_turn_off(self) -> None:
        await self._device.async_set_power(False, self._zone)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._device.async_set_volume_level(volume, self._zone)

    async def async_volume_up(self) -> None:
        await self._device.async_volume_step(True, self._zone)

    async def async_volume_down(self) -> None:
        await self._device.async_volume_step(False, self._zone)

    async def async_mute_volume(self, mute: bool) -> None:
        await self._device.async_set_mute(mute, self._zone)

    async def async_select_source(self, source: str) -> None:
        await self._device.async_set_source(source, self._zone)
