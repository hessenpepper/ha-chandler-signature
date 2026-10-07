# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Experiment: set a valve's clock from a PC and watch how the valve reacts.

WRITES to the valve (hour, minute and optionally seconds). Needs ``pip install bleak`` and
the valve must not be connected to anything else (stop the Home Assistant entry first).

    python tools/set_clock.py --name C2_0C --token-file token.txt [--no-seconds] [--watch 90]

It waits for the start of the next minute, writes that minute, then prints every clock
value the valve reports with the real time it arrived, so you can see whether the valve's
minute rollover moved to the top of the minute.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import pathlib
import sys
import types

from bleak import BleakClient, BleakScanner

PKG_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "chandler_signature"
pkg = types.ModuleType("chandler_signature")
pkg.__path__ = [str(PKG_DIR)]
sys.modules["chandler_signature"] = pkg

from chandler_signature.models import seconds_to_next_minute  # noqa: E402
from chandler_signature.protocol import WRITE_CHAR_UUID, parse_token  # noqa: E402
from chandler_signature.session import SignatureSession  # noqa: E402


def stamp() -> str:
    return dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--no-seconds", action="store_true", help="write only dh and dm")
    parser.add_argument("--watch", type=float, default=90, help="seconds to watch afterwards")
    parser.add_argument("--trace", action="store_true", help="print every raw packet")
    parser.add_argument(
        "--no-response", action="store_true", help="use write-without-response for all writes"
    )
    args = parser.parse_args()
    token = parse_token(pathlib.Path(args.token_file).read_text())

    device = await BleakScanner.find_device_by_filter(
        lambda d, adv: (adv.local_name or d.name or "") == args.name, timeout=20
    )
    if device is None:
        print("valve not found")
        return 2

    seen: list[tuple[str, int | None, int | None]] = []
    last = {"dh": None, "dm": None}

    def on_data(state: dict) -> None:
        pair = (state.get("dh"), state.get("dm"))
        if pair != (last["dh"], last["dm"]):
            last["dh"], last["dm"] = pair
            seen.append((stamp(), *pair))
            print(f"[{stamp()}] valve reports clock {pair[0]}:{pair[1]}")

    async with BleakClient(device, timeout=30) as client:
        char = client.services.get_characteristic(WRITE_CHAR_UUID)
        with_response = bool(char and "write" in char.properties) and not args.no_response
        session = SignatureSession(client, token, on_data, write_with_response=with_response)
        if args.trace:
            original_notify = session._on_notify
            original_write = client.write_gatt_char

            def traced_notify(characteristic, data):
                raw = bytes(data)
                if raw not in (b"\xe0",):  # hide the once-a-second keep-alive
                    print(f"[{stamp()}]   << {raw.hex()}")
                original_notify(characteristic, data)

            async def traced_write(characteristic, data, response=None):
                print(f"[{stamp()}]   >> {bytes(data).hex()}  (response={response})")
                return await original_write(characteristic, data, response=response)

            session._on_notify = traced_notify
            client.write_gatt_char = traced_write
        await session.start()
        await session.wait_authenticated(20)
        print(f"[{stamp()}] authenticated; mtu={getattr(client, 'mtu_size', '?')}; "
              f"valve clock {session.state.get('dh')}:{session.state.get('dm')}; "
              f"regen motor={session.state.get('dlr')} position={session.state.get('drcp')}")
        if session.state.get("dlr") or session.state.get("drcp"):
            print("valve is regenerating; not writing")
            await session.close()
            return 3

        now = dt.datetime.now()
        await asyncio.sleep(seconds_to_next_minute(now.second, now.microsecond) + 0.05)
        now = dt.datetime.now()
        payload = {"dh": now.hour, "dm": now.minute}
        if not args.no_seconds:
            payload["ds"] = 0
        max_packet = max(8, int(getattr(client, "mtu_size", 23)) - 3)
        print(f"[{stamp()}] writing {payload} (max packet {max_packet})")
        try:
            await session.send_json(payload, max_packet=max_packet)
            print(f"[{stamp()}] valve acknowledged the write")
        except Exception as err:  # noqa: BLE001
            print(f"[{stamp()}] write failed: {err}")
        await asyncio.sleep(args.watch)
        await session.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
