"""Remote entity for receivers using the JSON protocol."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import RemoteEntity
from homeassistant.exceptions import HomeAssistantError

from .jsondevice import SonyJsonDevice
from .jsonentity import SonyJsonEntity

_LOGGER = logging.getLogger(__name__)


class SonyJsonRemote(SonyJsonEntity, RemoteEntity):
    """Power and mute, plus raw feature writes via remote.send_command.

    Commands are strings of the form "feature=value", for example
    "audio.dll=auto1" or "system.dimmer=60".
    """

    _attr_name = "Remote"
    _features = ("main.power", "main.mute")

    def __init__(self, device: SonyJsonDevice) -> None:
        super().__init__(device)
        self._attr_unique_id = f"{device.unique_base}_remote"

    @property
    def is_on(self) -> bool | None:
        return self._device.power()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._device.async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._device.async_set_power(False)

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        for item in command:
            feature, sep, raw = item.partition("=")
            if not sep or not feature.strip():
                raise HomeAssistantError(f'Commands look like "feature=value", got {item!r}')
            if not await self._device.async_set_feature(
                feature.strip(), self._device.parse_value(raw.strip())
            ):
                raise HomeAssistantError(f"The receiver rejected {item}")
