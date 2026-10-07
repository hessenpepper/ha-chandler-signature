# Chandler Signature for Home Assistant

A Home Assistant custom integration for **Chandler Systems** water softeners and filters
with Bluetooth ("Signature" valves, firmware **C6.13 or newer**). It uses the Bluetooth API
that Chandler documents for third-party use, authenticated with the per-valve API token
that the Legacy View app generates.

> **Unofficial.** This project is not affiliated with or endorsed by Chandler Systems, Inc.
> "Chandler" and "Legacy View" belong to their owners. The integration is **read-only**: it
> never changes a setting on your valve.

## What you get

For each valve, a device with:

| Entity | Valves |
|---|---|
| Water used today, average daily water use, peak flow today | all |
| Treated water remaining | metered softeners |
| Salt remaining (lb), salt level (%), water hardness setting | softeners |
| Battery voltage (diagnostic) | valves that report a battery |
| Regeneration motor moving | all |

The device page also shows the valve type, serial number and firmware (for example `C6.18`).
Values are pushed by the valve, so they update as soon as they change. If the connection
drops, the entities become unavailable and the integration reconnects on its own.

## Requirements

* Home Assistant 2025.1 or newer with a working Bluetooth adapter or ESPHome Bluetooth
  proxy within range of the valve.
* Valve firmware **C6.13 or newer** (shown in the Legacy View app).
* The valve's **API token**: in the Legacy View app, open the valve's settings and enable the
  API. The app shows a token (a UUID). Disabling and re-enabling the API creates a new one.

## Installation

**HACS (custom repository):** HACS, three-dot menu, *Custom repositories*, add
`https://github.com/hessenpepper/ha-chandler-signature` as an *Integration*, install, and
restart Home Assistant.

**Manual:** copy `custom_components/chandler_signature` into your Home Assistant
`config/custom_components/` folder and restart.

## Setup

1. Go to *Settings, Devices & services*. Valves in range are offered automatically; otherwise
   choose *Add integration* and search for **Chandler Signature**.
2. Enter the valve's API token. Setup connects to the valve to verify it.
3. Repeat for each valve; every valve has its own token.

## Good to know

* **One connection at a time.** The valve talks to a single Bluetooth client, and this
  integration keeps its connection open. While Home Assistant is connected, the phone app
  may not be able to connect. Disable the integration entry to use the app.
* The token is stored in Home Assistant's config entry storage, like other credentials.
* Present flow rate is not exposed yet: no flow value appeared in the data while the valves
  were idle. Other data the valve sends (regeneration settings, history, graphs) could be
  added if there is interest.
* Tested by the author on two valves only, a metered softener and an aeration filter, both
  on firmware C6.18. Other valve types are supported on a best-effort basis.

## Development

```
pip install pytest pytest-asyncio
pytest
python tools/probe.py --list      # needs `pip install bleak`; talks to a real valve
```

The protocol layer (`protocol.py`, `session.py`, `models.py`) has no Home Assistant
dependency and is covered by offline tests that include a simulated valve.

## License

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
