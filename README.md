# Home Assistant to Sony AVR STR-DN1040, STR_DA5800ES, STR-DN850, STR-DN1060 & Other AVRs


[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![maintained](https://img.shields.io/maintenance/yes/2026.svg)](#)
[![maintainer](https://img.shields.io/badge/maintainer-%20%40bmannz-blue.svg)](#)
[![version](https://img.shields.io/github/v/release/bmannz/sonyavr)](#)

This is a fork from peteS-UK/sonyavr who did great work but it wouldn't work with my STR-ZA2000ES and had very limited functionality.
This custom component implements a media player entity and a remote entity for Home Assistant to allow for integration with Sony AVR-STR receivers.  It's developed with the STR-ZA1100ES.  

Online sources suggest similar commands may work with

| Year | AVR |
| --- | --- |
| 2016 | STR-ZA1100ES, STR-ZA2100ES, STR-ZA3100ES, STR-DN810ES, STR-DN1070 |
| 2015 | STR-DN1060, STR-ZA5000ES |
| 2014 | STR-ZA1000ES, STR-ZA2000ES, STR-ZA3000ES, STR-DN1050 |
| 2013 | STR-DN1040 |
| 2012 | STR-DA1800, STR-DA2800, STR-DA5800 |

but most of these are untested at present.  It's known not to work with STR-DN1030, which doesn't respond to the same commands or send feedback.  

For other AVRs, you may need to change the default port during configuration.  By default, the port is 33335, but some online sources suggest ports might be

| Port | AVR |
| --- | --- |
| 8080 | STR-DA1800 |
| 33336 | DN-1070 & CISv2 |
| 33335 | All Others |

The integration is a Local Push integration - i.e. it subscribes to notification of changes to the AVR, so doesn't need to periodically poll for its state.

## Network Standby
The integration requires Network Standby to be available and enabled on the AVR.  Please make sure Network Standby is enabled befoer installing the integration.

## JSON-protocol receivers (STR-ZA2000ES and other STR-ZA ES models)

Some receivers, including the STR-ZA2000ES, answer a JSON protocol on **port 33336**
(the byte protocol on 33335 is also open, but far more limited). When you add the
integration with port **33336**, it uses the JSON protocol instead, which provides:

* real state on startup and push updates (no more poking volume/mute/input to learn state)
* Main, Zone 2 and Zone 3 power, volume (dB), mute and input
* sound field selection (`2ch` and `multi` are confirmed; any other value the receiver reports is learned automatically)
* **Select** entities: Digital Legato Linear, Sound Optimizer, HD-D.C.S. type, Dual Mono, Dynamic Range Compression, HDMI 4K scaling / pass-through / audio out / Zone 2 audio out / Out 2 / zone priority / fast view, Zone 2 & 3 line out, display brightness
* **Switch** entities: Pure Direct, In-Ceiling Speaker Mode, HDMI control (CEC), Auto Display, Auto Standby
* **Number** entities (disabled by default): speaker levels and bass/treble trims
* **Sensors**: main volume (dB), tuner frequency and preset, firmware version, IP address, speaker layout
* a `send_command` service with **Set Feature** (`audio.dll=auto1`) and **Get Feature** (`audio.dll`) for anything not exposed as an entity

Requirements on the receiver: **Setup > Network Setup > Network Standby = On** and
**Setup > Install Setup > External Control = On**.

Settings that the receiver cannot change right now (for example HDMI control, or Zone 2 volume
while Zone 2 is off) show as *unavailable* rather than showing a wrong value.

### Volume limits

The receiver is controlled in dB. The slider maps -80 dB to 0 %, and **Maximum Allowed Volume**
(options, default **0 dB**) to 100 %. Zone 2/3 have their own limit (default +20 dB).
The integration never sends a volume above these limits. Change them under
*Configure > Configuration*.

### Known limits

* Power on/off, input changes and sound field changes use the same `set` messages as volume and
  mute, but were not exercised during protocol discovery. If one is rejected, the Home Assistant
  log shows the receiver's reply (`NAK` or `ERR`).
* Sound fields other than `2ch` and `multi` appear once the receiver reports them. `A.F.D.` and
  `Movie` are listed from the web remote's button names and may need adjusting.
* The receiver also serves a web setup page on port 80. It uses the same feature names over
  HTTP (`POST /request.cgi`), which is how the settings above were identified.

### Tests

`python3 -m unittest tests.test_json_backend tests.test_entities` runs the protocol and entity
tests against a mock receiver that replays real STR-ZA2000ES replies. They do not need Home Assistant.

## Installation

The preferred installation approach is via Home Assistant Community Store - aka [HACS](https://hacs.xyz/).  

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=peteS-UK&repository=sonyavr&category=integration)

If you want to download the integration manually, create a new folder called sonyavr under your custom_components folder in your config folder.  If the custom_components folder doesn't exist, create it first.  Once created, download the files and folders from the [github repo](https://github.com/peteS-UK/sonyavr/tree/main/custom_components/sonyavr) into this new sonyavr folder.

Once downloaded either via HACS or manually, restart your Home Assistant server.

## Configuration

Configuration is done through the Home Assistant UI.  Once you're installed the integration, go into your Integrations (under Settings, Devices & Services), select Add Integration, and choose the SONY AVR integration, or click the shortcut button below (requires My Homeassistant configured).

[![Add Integration to your Home Assistant
instance.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=sonyavr)

This will display the configuration page.  

### Manual Entry
You need to enter the details of your processor manually - IP address, name, model and port.

When you select Submit, the configuration will setup the components in Home Assistant.  It will create one device, two entities and a service.

## Device & Entities
A device will be created with the name given during setup.

### Media Player entity
A media player entity will be created with a default entity_id of media_player.sonyavr.  

You can control power state, volume, muting, source and sound mode from the media player.  You can also use this entity from any card for media player.

### Remote entity
A remote entity will be created with a default entity_id of remote.sonyavr.

This entity only supports power on and off, and mute on and off functions.  It doesn't support sending commands directly, since the actual commands for the AVR are just byte strings.  Please use the Send Command service below.

### Sensor entity
A sensor entity will be created with a default entity_id of sensor.sonyavr_volume which shows the current volume of the AVR.

### Zone 2 / Zone 3 media player entities (optional)
If your AVR has Zone 2 or Zone 3, you can add a media player entity for each zone.  Zones are off by default; to add them, open the integration's Configure options, choose Configuration and select the zones you want under Zones.  The integration reloads and adds (or removes) the zone entities on the same device.

Each zone entity supports power, volume (set and step), mute and source.  The source list includes Source, which makes the zone follow the main zone's input, as well as the inputs shown for the main zone, using any custom input names.

The AVR doesn't report a zone's state on request, so when Home Assistant starts, the zone's volume is read but its power and source show as unknown until they next change, either from Home Assistant or from the AVR or its remote.  Zone support has been tested with the STR-ZA1100ES.

## Sony AVR. Send Command

The integration provides a service which allows you to send commands to the AVR.  This service provides you with a dropdown list of all of the available commands.


### Media Player State

The integration tracks the the state of the volume on the AVR and creates and maintains an attribute on the media_player.sonyavr entity.  This is the volume as displayed on the AVR, rather than the percentage volume which Home Assistant provides.

## References

The integration is based on work done at https://gist.github.com/IceEyz/c55d36614d58c006ad698afbb98b7099, which itself was derived from https://github.com/aschaeffer/sony-av-indicator.
