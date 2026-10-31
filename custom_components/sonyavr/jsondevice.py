"""Device model for receivers using the JSON protocol (e.g. STR-ZA2000ES).

Sits between the protocol client (jsonclient.py) and the Home Assistant
entities. It has no hard Home Assistant dependency (the storage helper is
imported lazily) so the logic can be unit tested with a mock receiver.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_MAX_VOLUME,
    CONF_SOUND_MODES,
    CONF_SOURCE_NAMES,
    CONF_SOURCES,
    CONF_ZONE_MAX_VOLUME,
    DEFAULT_MAX_VOLUME_DB,
    DEFAULT_ZONE_MAX_VOLUME_DB,
    DOMAIN,
    MIN_VOLUME_DB,
    VOLUME_STEP_DB,
)
from .jsonclient import (
    ACK,
    CONNECTION,
    SonyJsonClient,
    SonyJsonConnectionError,
)
from .source_names import default_name, display_list, display_name

_LOGGER = logging.getLogger(__name__)

MAIN = "main"
ZONES = ("zone2", "zone3")

# Input values come from the receiver's own web UI (preset page). Order = UI order.
INPUT_NAMES: dict[str, str] = {
    "bd": "BD/DVD",
    "sat": "SAT/CATV",
    "game": "Game",
    "stb": "STB",
    "video": "Video",
    "aux": "AUX",
    "tv": "TV",
    "cd": "SA-CD/CD",
    "fm": "FM Tuner",
    "am": "AM Tuner",
}

# "2ch" and "multi" were observed on a real receiver. "afd" and "movie" are
# inferred from the web remote's buttons and are unverified; any other value
# the receiver reports is learned automatically and added to the list.
SOUND_FIELD_NAMES: dict[str, str] = {
    "2ch": "2ch Stereo",
    "multi": "Multi Ch Stereo",
    "afd": "A.F.D.",
    "movie": "Movie",
}


@dataclass(frozen=True)
class SelectDef:
    feature: str
    name: str
    options: tuple[str, ...]
    labels: dict[str, str] | None = None
    icon: str | None = None
    numeric: bool = False  # receiver stores the value as a number


@dataclass(frozen=True)
class SwitchDef:
    feature: str
    name: str
    icon: str | None = None


@dataclass(frozen=True)
class NumberDef:
    feature: str
    name: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None


SELECTS: tuple[SelectDef, ...] = (
    SelectDef("audio.dll", "Digital Legato Linear", ("auto1", "auto2", "off"),
              {"auto1": "Auto 1", "auto2": "Auto 2", "off": "Off"}, "mdi:waveform"),
    SelectDef("audio.soundoptimizer", "Sound Optimizer", ("normal", "low", "off"),
              {"normal": "Normal", "low": "Low", "off": "Off"}, "mdi:tune-vertical"),
    SelectDef("audio.hddcstype", "HD-D.C.S. Type", ("dynamic", "theater", "studio"),
              {"dynamic": "Dynamic", "theater": "Theater", "studio": "Studio"}, "mdi:surround-sound"),
    SelectDef("audio.dualmono", "Dual Mono", ("main", "sub", "main_sub"),
              {"main": "Main", "sub": "Sub", "main_sub": "Main/Sub"}, "mdi:numeric-2-box-multiple"),
    SelectDef("audio.drangecomp", "Dynamic Range Compression", ("auto", "on", "off"),
              {"auto": "Auto", "on": "On", "off": "Off"}, "mdi:arrow-collapse-vertical"),
    SelectDef("hdmi.4kscaling", "HDMI 4K Scaling", ("auto", "off"),
              {"auto": "Auto", "off": "Off"}, "mdi:video-4k-box"),
    SelectDef("hdmi.passthrough", "HDMI Pass-through", ("auto", "on", "off"),
              {"auto": "Auto", "on": "On", "off": "Off"}, "mdi:hdmi-port"),
    SelectDef("hdmi.audioout", "HDMI Audio Out", ("amp", "tvamp"),
              {"amp": "AMP", "tvamp": "TV + AMP"}, "mdi:hdmi-port"),
    SelectDef("hdmi.zone2audioout", "HDMI Zone 2 Audio Out", ("amp", "zone2tv", "zone2amp"),
              {"amp": "AMP", "zone2tv": "Zone 2 TV + AMP", "zone2amp": "Zone 2 AMP"}, "mdi:hdmi-port"),
    SelectDef("hdmi.out2", "HDMI Out 2 Assignment", ("main", "zone2"),
              {"main": "Main", "zone2": "Zone 2"}, "mdi:hdmi-port"),
    SelectDef("hdmi.zonepriority", "HDMI Zone Priority", ("zone2main", "main"),
              {"zone2main": "Main & Zone 2", "main": "Main Only"}, "mdi:hdmi-port"),
    SelectDef("hdmi.fastview", "HDMI Fast View", ("auto", "off"),
              {"auto": "Auto", "off": "Off"}, "mdi:fast-forward"),
    SelectDef("zone2.lineout", "Zone 2 Line Out", ("variable", "fixed"),
              {"variable": "Variable", "fixed": "Fixed"}, "mdi:audio-input-rca"),
    SelectDef("zone3.lineout", "Zone 3 Line Out", ("variable", "fixed"),
              {"variable": "Variable", "fixed": "Fixed"}, "mdi:audio-input-rca"),
    SelectDef("system.dimmer", "Display Brightness", ("100", "60", "0"),
              {"100": "100 %", "60": "60 %", "0": "0 % (display off)"}, "mdi:brightness-6",
              numeric=True),
)

SWITCHES: tuple[SwitchDef, ...] = (
    SwitchDef("audio.puredirect", "Pure Direct", "mdi:alpha-p-box"),
    SwitchDef("audio.inceilingmode", "In-Ceiling Speaker Mode", "mdi:ceiling-light"),
    SwitchDef("hdmi.cec", "HDMI Control (CEC)", "mdi:remote-tv"),
    SwitchDef("system.autodisplay", "Auto Display", "mdi:monitor-eye"),
    SwitchDef("system.autoStandby", "Auto Standby", "mdi:sleep"),
)

# Speaker trims. Ranges are the receiver's documented +/-10 (dB) and are only
# enforced by the receiver itself; entities are disabled by default.
NUMBERS: tuple[NumberDef, ...] = tuple(
    [NumberDef(f"level.{ch}", f"Level {label}", -10.0, 10.0, 0.5, "dB")
     for ch, label in (("FL", "Front Left"), ("FR", "Front Right"), ("CR", "Center"),
                       ("SL", "Surround Left"), ("SR", "Surround Right"),
                       ("SBL", "Surround Back Left"), ("SBR", "Surround Back Right"),
                       ("SB", "Surround Back"), ("FHL", "Front High Left"),
                       ("FHR", "Front High Right"), ("SW", "Subwoofer"))]
    + [NumberDef(f"{kind}.{grp}", f"{kind.title()} {label}", -10.0, 10.0, 1.0)
       for kind in ("bass", "treble")
       for grp, label in (("front", "Front"), ("center", "Center"), ("surround", "Surround"))]
)

SENSOR_FEATURES = (
    "system.version", "network.ipaddress", "network.macaddress", "speaker.pattern",
    "tuner.frequency", "tuner.band", "tuner.preset",
)

CORE = (
    "main.power", "main.input", "main.volumedb", "main.mute", "audio.soundfield",
    "zone2.power", "zone2.input", "zone2.volumedb", "zone2.mute",
    "zone3.power", "zone3.input", "zone3.volumedb", "zone3.mute",
)
SETTINGS = tuple(d.feature for d in SELECTS) + tuple(d.feature for d in SWITCHES)
TRIMS = tuple(d.feature for d in NUMBERS)
TUNER = ("tuner.frequency", "tuner.band", "tuner.preset")
INFO = ("system.version", "network.ipaddress", "network.macaddress", "speaker.pattern")

SETTLE_DELAY = 3.0  # seconds after a power change before re-reading settings
SLOW_REFRESH_EVERY = 10  # periodic ticks between settings refreshes


def _sanitise(name: str) -> str:
    return name.replace(" ", "_").replace("-", "_").replace(":", "_")


class SonyJsonDevice:
    """One receiver."""

    def __init__(
        self,
        hass: Any,
        entry: Any,
        host: str,
        name: str,
        model: str,
        port: int,
        *,
        spawn: Callable[..., Any] | None = None,
        client: SonyJsonClient | None = None,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.name = name
        self.model = model
        self.unique_base = "sonyavr_" + _sanitise(name)
        self.client = client or SonyJsonClient(host, port, spawn=spawn)
        self._spawn = spawn
        self._learned: dict[str, list[str]] = {"inputs": [], "soundfields": []}
        self._store: Any = None
        self._ticks = 0
        self._seen_power: dict[str, Any] = {}
        self._settle_task: asyncio.Task | None = None
        self._option_listeners: list[Callable[[set[str]], None]] = []
        self.client.on_connect = self.async_refresh_all
        self.client.on_periodic = self._async_periodic

    # --------------------------------------------------------------- lifecycle

    @property
    def options(self) -> dict[str, Any]:
        return dict(getattr(self.entry, "options", None) or {})

    async def async_start(self) -> None:
        if self.hass is not None and self._store is None:
            from homeassistant.helpers.storage import Store  # noqa: PLC0415

            self._store = Store(self.hass, 1, f"{DOMAIN}_{self.entry.entry_id}_json")
            data = await self._store.async_load()
            if isinstance(data, dict):
                for key in self._learned:
                    values = data.get(key)
                    if isinstance(values, list):
                        self._learned[key] = [v for v in values if isinstance(v, str)]
        self.client.add_listener(self._on_change)
        await self.client.start()

    async def async_stop(self) -> None:
        if self._settle_task is not None:
            self._settle_task.cancel()
        await self.client.stop()

    def async_options_updated(self) -> None:
        """Entities re-read options (source lists, names, volume limits)."""
        self.client._notify({CONNECTION})

    # ------------------------------------------------------------------ state

    @property
    def available(self) -> bool:
        return self.client.connected

    def value(self, feature: str) -> Any:
        return self.client.state.get(feature)

    def feature_available(self, feature: str) -> bool:
        return self.client.connected and feature in self.client.state

    def power(self, zone: str = MAIN) -> bool | None:
        value = self.value(f"{zone}.power")
        if value is None:
            return None
        return value == "on"

    def muted(self, zone: str = MAIN) -> bool | None:
        value = self.value(f"{zone}.mute")
        return None if value is None else value == "on"

    # ----------------------------------------------------------------- volume

    def volume_bounds(self, zone: str = MAIN) -> tuple[float, float]:
        opts = self.options
        if zone == MAIN:
            maximum = opts.get(CONF_MAX_VOLUME)
            maximum = DEFAULT_MAX_VOLUME_DB if maximum is None else float(maximum)
        else:
            maximum = opts.get(CONF_ZONE_MAX_VOLUME)
            maximum = DEFAULT_ZONE_MAX_VOLUME_DB if maximum is None else float(maximum)
        minimum = MIN_VOLUME_DB
        if maximum <= minimum:
            maximum = minimum + 1.0
        return minimum, maximum

    def volume_db(self, zone: str = MAIN) -> float | None:
        value = self.value(f"{zone}.volumedb")
        return float(value) if isinstance(value, (int, float)) else None

    def volume_level(self, zone: str = MAIN) -> float | None:
        db = self.volume_db(zone)
        if db is None:
            return None
        minimum, maximum = self.volume_bounds(zone)
        return round(min(max((db - minimum) / (maximum - minimum), 0.0), 1.0), 3)

    def clamp_volume(self, db: float, zone: str = MAIN) -> float:
        minimum, maximum = self.volume_bounds(zone)
        return round(min(max(db, minimum), maximum) * 2) / 2  # 0.5 dB steps

    async def async_set_volume_db(self, db: float, zone: str = MAIN) -> bool:
        return await self._set(f"{zone}.volumedb", self.clamp_volume(db, zone))

    async def async_set_volume_level(self, level: float, zone: str = MAIN) -> bool:
        minimum, maximum = self.volume_bounds(zone)
        return await self.async_set_volume_db(minimum + level * (maximum - minimum), zone)

    async def async_volume_step(self, up: bool, zone: str = MAIN) -> bool:
        current = self.volume_db(zone)
        if current is None:
            return False
        step = VOLUME_STEP_DB if up else -VOLUME_STEP_DB
        return await self.async_set_volume_db(current + step, zone)

    # ------------------------------------------------------------ power / mute

    async def async_set_power(self, on: bool, zone: str = MAIN) -> bool:
        return await self._set(f"{zone}.power", "on" if on else "off", read_back=False)

    async def async_set_mute(self, mute: bool, zone: str = MAIN) -> bool:
        return await self._set(f"{zone}.mute", "on" if mute else "off", read_back=False)

    # ---------------------------------------------------------------- sources

    def _raw_inputs(self) -> list[str]:
        raw = list(INPUT_NAMES)
        raw += [v for v in self._learned["inputs"] if v not in INPUT_NAMES]
        return raw

    def _raw_soundfields(self) -> list[str]:
        raw = list(SOUND_FIELD_NAMES)
        raw += [v for v in self._learned["soundfields"] if v not in SOUND_FIELD_NAMES]
        return raw

    @staticmethod
    def _friendly(raw: str, names: dict[str, str]) -> str:
        return names.get(raw) or raw.replace("_", " ").title()

    def input_friendly(self, raw: str) -> str:
        return self._friendly(raw, INPUT_NAMES)

    def soundfield_friendly(self, raw: str) -> str:
        return self._friendly(raw, SOUND_FIELD_NAMES)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(self.input_friendly(r) for r in self._raw_inputs())

    def source_list(self) -> tuple[str, ...]:
        opts = self.options
        return display_list(self.sources, opts.get(CONF_SOURCES), opts.get(CONF_SOURCE_NAMES))

    def source(self, zone: str = MAIN) -> str | None:
        raw = self.value(f"{zone}.input")
        if not isinstance(raw, str):
            return None
        return display_name(self.input_friendly(raw), self.options.get(CONF_SOURCE_NAMES))

    def raw_input(self, shown: str) -> str | None:
        """Map a (possibly renamed) displayed source back to the receiver's value."""
        friendly = default_name(shown, self.options.get(CONF_SOURCE_NAMES))
        for raw in self._raw_inputs():
            if self.input_friendly(raw) == friendly or raw == shown:
                return raw
        return None

    async def async_set_source(self, shown: str, zone: str = MAIN) -> bool:
        raw = self.raw_input(shown)
        if raw is None:
            _LOGGER.error("%r is not a known input", shown)
            return False
        return await self._set(f"{zone}.input", raw, read_back=False)

    async def async_source_step(self, up: bool, zone: str = MAIN) -> bool:
        shown = list(self.source_list())
        current = self.source(zone)
        if not shown:
            return False
        index = shown.index(current) if current in shown else -1
        target = shown[(index + (1 if up else -1)) % len(shown)]
        return await self.async_set_source(target, zone)

    # ------------------------------------------------------------ sound field

    def sound_mode_list(self) -> tuple[str, ...]:
        visible = self.options.get(CONF_SOUND_MODES)
        modes = [self.soundfield_friendly(r) for r in self._raw_soundfields()]
        return tuple(m for m in modes if not visible or m in visible)

    def sound_mode(self) -> str | None:
        raw = self.value("audio.soundfield")
        return self.soundfield_friendly(raw) if isinstance(raw, str) else None

    def raw_soundfield(self, shown: str) -> str | None:
        for raw in self._raw_soundfields():
            if self.soundfield_friendly(raw) == shown or raw == shown:
                return raw
        return None

    async def async_set_sound_mode(self, shown: str) -> bool:
        raw = self.raw_soundfield(shown)
        if raw is None:
            _LOGGER.error("%r is not a known sound field", shown)
            return False
        return await self._set("audio.soundfield", raw, read_back=False)

    # -------------------------------------------------------- generic features

    async def async_set_feature(self, feature: str, value: Any) -> bool:
        return await self._set(feature, value)

    async def async_get_feature(self, feature: str) -> Any:
        return await self.client.get(feature)

    async def _set(self, feature: str, value: Any, read_back: bool = True) -> bool:
        try:
            ok = await self.client.set(feature, value) == ACK
            if ok and read_back:
                await self.client.get(feature)
            return ok
        except SonyJsonConnectionError as err:
            _LOGGER.warning("Cannot set %s: %s", feature, err)
            return False

    # ---------------------------------------------------------------- refresh

    async def _get_all(self, features: tuple[str, ...]) -> None:
        for feature in features:
            await self.client.get(feature)

    async def async_refresh_all(self) -> None:
        self._ticks = 0
        await self._get_all(CORE + INFO + SETTINGS + TUNER + TRIMS)

    async def async_refresh_core(self) -> None:
        await self._get_all(CORE)

    async def _async_periodic(self) -> None:
        self._ticks += 1
        await self._get_all(CORE)
        if self._ticks % SLOW_REFRESH_EVERY == 0:
            await self._get_all(SETTINGS + TUNER + TRIMS)

    def _on_change(self, changed: set[str]) -> None:
        values = self.client.state
        for feature, key, known in (
            ("main.input", "inputs", INPUT_NAMES),
            ("zone2.input", "inputs", INPUT_NAMES),
            ("zone3.input", "inputs", INPUT_NAMES),
            ("audio.soundfield", "soundfields", SOUND_FIELD_NAMES),
        ):
            value = values.get(feature)
            if (
                feature in changed
                and isinstance(value, str)
                and value not in known
                and value not in self._learned[key]
            ):
                _LOGGER.info("Learned new %s value %r from the receiver", key, value)
                self._learned[key].append(value)
                if self._store is not None:
                    self._store.async_delay_save(lambda: dict(self._learned), 1)
        # A real power change makes many settings (un)available; re-read them
        # shortly. The first value seen for a zone (initial refresh) is not a change.
        power_changed = False
        for feature in changed & {"main.power", "zone2.power", "zone3.power"}:
            current = values.get(feature)
            previous = self._seen_power.get(feature)
            self._seen_power[feature] = current
            if previous is not None and previous != current:
                power_changed = True
        if power_changed and self.client.connected:
            self._schedule_settle()

    def _schedule_settle(self) -> None:
        if self._settle_task is not None and not self._settle_task.done():
            return

        async def settle() -> None:
            await asyncio.sleep(SETTLE_DELAY)
            try:
                await self.async_refresh_core()
                await self._get_all(SETTINGS + TUNER)
            except SonyJsonConnectionError:
                pass

        if self._spawn is not None:
            self._settle_task = self._spawn(settle(), "sonyavr settle")
        else:
            self._settle_task = asyncio.get_running_loop().create_task(settle())

    # ------------------------------------------------------------- raw helpers

    @staticmethod
    def parse_value(text: str) -> Any:
        """Parse a service value: JSON if possible (numbers), else a string."""
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text
