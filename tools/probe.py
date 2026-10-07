# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Talk to a real valve from a PC using the integration's own protocol code.

Needs ``pip install bleak`` and a Bluetooth adapter in range. Read-only.

    python tools/probe.py --list
    python tools/probe.py --name C2_01 --token <API token> [--seconds 15]
    python tools/probe.py --name C2_01 --token-file path/to/token.txt

Prefer --token-file so the token does not end up in your shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import types

from bleak import BleakClient, BleakScanner

# Import the integration's pure-Python modules without importing Home Assistant.
PKG_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "chandler_signature"
pkg = types.ModuleType("chandler_signature")
pkg.__path__ = [str(PKG_DIR)]
sys.modules["chandler_signature"] = pkg

from chandler_signature.models import (  # noqa: E402
    battery_volts,
    firmware_version,
    salt_percent,
    scaled,
    serial_number,
    valve_type_name,
)
from chandler_signature.protocol import (  # noqa: E402
    MANUFACTURER_ID,
    WRITE_CHAR_UUID,
    parse_token,
)
from chandler_signature.session import SignatureSession  # noqa: E402


async def list_valves() -> None:
    found = {}

    def callback(device, adv):
        name = adv.local_name or device.name or ""
        if MANUFACTURER_ID in adv.manufacturer_data or name.startswith(("C2_", "CS_")):
            found[device.address] = (name, adv.rssi)

    async with BleakScanner(callback):
        await asyncio.sleep(10)
    for address, (name, rssi) in found.items():
        print(f"{address}  {name!r}  rssi={rssi}")


async def probe(name: str, token: bytes, seconds: float) -> int:
    device = await BleakScanner.find_device_by_filter(
        lambda d, adv: (adv.local_name or d.name or "") == name, timeout=15
    )
    if device is None:
        print("valve not found; move closer or close the phone app")
        return 2
    async with BleakClient(device, timeout=30) as client:
        char = client.services.get_characteristic(WRITE_CHAR_UUID)
        session = SignatureSession(
            client, token, write_with_response=bool(char and "write" in char.properties)
        )
        await session.start()
        await session.wait_authenticated(20)
        await asyncio.sleep(seconds)
        state = dict(session.state)
        await session.close()

    print(f"{valve_type_name(state)}  firmware {firmware_version(state)}  serial {serial_number(state)}")
    for label, value in (
        ("water used today (gal)", scaled(state, "dwu", 100)),
        ("average daily use (gal)", scaled(state, "dwau", 100)),
        ("peak flow today (gpm)", scaled(state, "dpfd", 100)),
        ("treated water left (gal)", scaled(state, "dtgr", 100)),
        ("salt (lb)", scaled(state, "dbtr", 10)),
        ("salt level (%)", salt_percent(state)),
        ("hardness (gpg)", scaled(state, "dwh", 1)),
        ("battery (V)", battery_volts(state)),
    ):
        print(f"  {label:26} {value}")
    print(f"  keys received: {len(state)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--name")
    parser.add_argument("--token")
    parser.add_argument("--token-file")
    parser.add_argument("--seconds", type=float, default=15)
    args = parser.parse_args()
    if args.list or not args.name:
        asyncio.run(list_valves())
        return 0
    text = args.token or pathlib.Path(args.token_file).read_text()
    return asyncio.run(probe(args.name, parse_token(text), args.seconds))


if __name__ == "__main__":
    sys.exit(main())
