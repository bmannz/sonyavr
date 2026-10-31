"""The emotiva component."""

import logging

from homeassistant import config_entries, core
from homeassistant.const import CONF_HOST, CONF_MODEL, CONF_NAME, CONF_PORT, Platform

from .const import CONF_MAX_VOLUME, CONF_ZONES, DOMAIN, JSON_PORT
from .jsondevice import SonyJsonDevice
from .sonyavr import SonyAVR

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.REMOTE, Platform.SENSOR]
# Receivers on the JSON protocol (port 33336) get the richer entity set
PLATFORMS_JSON = [
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.NUMBER,
]


async def async_setup_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Set up platform from a ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})
    hass_data = dict(entry.data)

    # Registers update listener to update config entry when options are updated.
    unsub_options_update_listener = entry.add_update_listener(options_update_listener)
    # Store a reference to the unsubscribe function to cleanup if an entry is unloaded.
    hass_data["unsub_options_update_listener"] = unsub_options_update_listener

    _LOGGER.debug("Adding %s from config", hass_data[CONF_HOST])

    port = int(hass_data.get(CONF_PORT, "33335"))
    if port == JSON_PORT:
        device = SonyJsonDevice(
            hass,
            entry,
            hass_data[CONF_HOST],
            hass_data[CONF_NAME],
            hass_data[CONF_MODEL],
            port,
            spawn=lambda coro, name: hass.async_create_background_task(coro, name=name),
        )
        hass_data["sonyavr"] = device
        hass_data["json"] = True
        hass.data[DOMAIN][entry.entry_id] = hass_data
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_JSON)
        await device.async_start()
        return True

    sonyavr = SonyAVR(
        hass,
        entry,
        hass_data[CONF_HOST],
        hass_data[CONF_NAME],
        hass_data[CONF_MODEL],
        port,
    )

    if entry.options.get(CONF_MAX_VOLUME):
        _update_max_volume(sonyavr, entry.options.get(CONF_MAX_VOLUME))

    hass_data["sonyavr"] = sonyavr

    hass.data[DOMAIN][entry.entry_id] = hass_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _update_max_volume(sonyavr, value):
    try:
        vol = int(value)
        if vol is not None:
            sonyavr.volume_max = vol
    except Exception:
        pass


async def options_update_listener(
    hass: core.HomeAssistant, config_entry: config_entries.ConfigEntry
):
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    if entry_data.get("json"):
        if sorted(config_entry.options.get(CONF_ZONES, [])) != entry_data.get("zones", []):
            hass.config_entries.async_schedule_reload(config_entry.entry_id)
            return
        entry_data["sonyavr"].async_options_updated()
        return

    if sorted(config_entry.options.get(CONF_ZONES, [])) != entry_data.get("zones", []):
        # Zone entities are created at setup, so reload to add or remove them
        hass.config_entries.async_schedule_reload(config_entry.entry_id)
        return

    sonyavr = entry_data["sonyavr"]
    if config_entry.options.get(CONF_MAX_VOLUME) is not None:
        _update_max_volume(sonyavr, config_entry.options[CONF_MAX_VOLUME])

    # Refresh the entity so option changes such as the input list show
    # immediately rather than on the next AVR feedback.
    if sonyavr._update_cb:
        sonyavr._update_cb()

    """Handle options update."""
    # await hass.config_entries.async_reload(config_entry.entry_id)


async def async_unload_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload a config entry."""
    is_json = hass.data[DOMAIN][entry.entry_id].get("json", False)
    platforms = PLATFORMS_JSON if is_json else PLATFORMS
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, platforms):
        # Remove config entry from domain.
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        if is_json:
            await entry_data["sonyavr"].async_stop()
        # Remove options_update_listener.
        entry_data["unsub_options_update_listener"]()

    return unload_ok
