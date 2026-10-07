# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Experiment: find out which packet layout a valve accepts for a write.

WRITES to the valve (the current hour and minute, in several packet layouts). The valve
must not be connected to anything else. Prints the valve's reply to each variant:
ACK (0xCC) means accepted for processing, NAK (0xC8) means malformed.

    python tools/write_variants.py --name C2_0C --token-file token.txt
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

from chandler_signature.protocol import READ_CHAR_UUID, WRITE_CHAR_UUID, crc16, parse_token  # noqa: E402
from chandler_signature.session import SignatureSession  # noqa: E402

NAMES = {0xCC: "ACK", 0xC8: "NAK", 0xE0: "keep-alive", 0xF0: "polo"}


def stamp() -> str:
    return dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def variants(now: dt.datetime) -> dict[str, bytes]:
    body = f'{{"dh":{now.hour},"dm":{now.minute}}}'.encode()
    out: dict[str, bytes] = {}

    def le(c: int) -> bytes:
        return bytes([c & 0xFF, c >> 8])

    def be(c: int) -> bytes:
        return bytes([c >> 8, c & 0xFF])

    h = bytes([0xC0])
    out["A header+json, CRC low byte first (current)"] = h + body + le(crc16(h + body))
    out["B header+json, CRC high byte first"] = h + body + be(crc16(h + body))
    out["C json only, CRC low byte first"] = h + body + le(crc16(body))
    out["D json only, CRC high byte first"] = h + body + be(crc16(body))
    out["E header+json, seed 0, low byte first"] = h + body + le(crc16(h + body, 0))
    out["F header+json, no CRC"] = h + body
    out["G header+json, CRC then NUL"] = h + body + le(crc16(h + body)) + b"\x00"
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args()
    token = parse_token(pathlib.Path(args.token_file).read_text())

    device = await BleakScanner.find_device_by_filter(
        lambda d, adv: (adv.local_name or d.name or "") == args.name, timeout=20
    )
    if device is None:
        print("valve not found")
        return 2

    async with BleakClient(device, timeout=30) as client:
        char = client.services.get_characteristic(WRITE_CHAR_UUID)
        session = SignatureSession(
            client, token, write_with_response=bool(char and "write" in char.properties)
        )
        replies: list[tuple[str, bytes]] = []
        original = session._on_notify

        def tap(characteristic, data):
            raw = bytes(data)
            if raw != b"\xe0":
                replies.append((stamp(), raw))
            original(characteristic, data)

        session._on_notify = tap
        await session.start()
        await session.wait_authenticated(20)
        if session.state.get("dlr") or session.state.get("drcp"):
            print("valve is regenerating; not writing")
            await session.close()
            return 3
        print(f"[{stamp()}] authenticated; valve clock {session.state.get('dh')}:{session.state.get('dm')}")
        await asyncio.sleep(2)

        for label, packet in variants(dt.datetime.now()).items():
            replies.clear()
            print(f"[{stamp()}] {label}: >> {packet.hex()}")
            await client.write_gatt_char(WRITE_CHAR_UUID, packet, response=bool(char and "write" in char.properties))
            await asyncio.sleep(1.6)
            got = [(t, r) for t, r in replies if len(r) == 1]
            if not got:
                print("      no reply")
            for t, r in got:
                print(f"      [{t}] << {r.hex()} {NAMES.get(r[0], '')}")
        await session.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
