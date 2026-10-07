# Chandler Signature for Home Assistant

A Home Assistant custom integration for **Chandler Systems** water softeners and filters
with Bluetooth ("Signature" valves, firmware **C6.13 or newer**). It uses the Bluetooth API
that Chandler documents for third-party use, authenticated with the per-valve API token
that the Legacy View app generates.

> **Unofficial.** This project is not affiliated with or endorsed by Chandler Systems, Inc.
> "Chandler" and "Legacy View" belong to their owners. The integration only reads from your
> valve, with one exception: the **Sync clock** button, which sets the valve's clock and does
> nothing until you press it.

## What you get

For each valve, a device with:

| Entity | Valves |
|---|---|
| Water used today, average daily water use, peak flow today | all |
| Water used total (keeps counting across the daily reset) | all |
| Treated water remaining | metered softeners |
| Salt remaining (lb), salt level (%), water hardness setting | softeners |
| Battery voltage (diagnostic) | valves that report a battery |
| Regeneration motor moving | all |
| Valve clock (diagnostic), Sync clock (button) | all |

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

## Sync clock

A valve's clock decides when it regenerates (for example "2:00 AM"), so a wrong clock moves
that. **Valve clock** shows the valve's time so you can spot drift; **Sync clock** sets the
valve to Home Assistant's local time. It waits for the start of the next minute (so a press can
take up to a minute) and then writes that hour and minute with seconds set to 0, so the valve's
seconds start in step with real time. It refuses while the valve is regenerating. It is the
only write the integration performs.

## Energy dashboard (water)

The valve only reports "water used today", which resets at the valve's midnight. **Water used
total** adds those daily counts up into a lifetime total (it starts at zero when the entity is
first created), which is what the Energy dashboard's water settings need.

If one valve sees all of a building's water and another only part of it (for example a filter
upstream of a softener, with outside hose bibs on the filter only), the difference of the two
totals is the water that bypassed the second valve. A template sensor works well for that:

```yaml
template:
  - sensor:
      - name: Outside water used
        unique_id: outside_water_used
        unit_of_measurement: gal
        device_class: water
        state_class: total   # not total_increasing: small dips between updates are normal
        state: >
          {{ (states('sensor.filter_water_used_total') | float
              - states('sensor.softener_water_used_total') | float) | round(2) }}
        availability: >
          {{ states('sensor.filter_water_used_total') | is_number
             and states('sensor.softener_water_used_total') | is_number }}
```

Use the upstream valve as the Energy dashboard's water source and the others as individual
water devices. A valve with a wrong clock only changes *when* its "today" counter resets; the
lifetime totals still add up real usage. The one gap: water used while Home Assistant is
disconnected across a valve's midnight reset is counted from the reset onward.

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
