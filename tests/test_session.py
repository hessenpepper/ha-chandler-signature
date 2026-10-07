# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Exercise SignatureSession against a fake valve that follows the documented flow."""

import asyncio

import pytest

from chandler_signature.protocol import (
    ACK,
    ID_PACKET,
    MARCO,
    POLO,
    READ_CHAR_UUID,
    RESET_COMMAND,
    WRITE_CHAR_UUID,
    encode_json_packets,
)
from chandler_signature.session import InvalidAuth, SignatureSession

TOKEN = bytes.fromhex("8dffdfbd40cc4b868df81d48463b20f1")
DEVICE_LIST = {"dlf": 618, "dlvt": 1, "dlsa": 1, "dlsb": 2}
DASHBOARD = {"as": 2, "dwu": 1900, "dtgr": 32925}


class FakeValve:
    """Minimal Bleak-like client that behaves like a valve."""

    def __init__(self, good_token: bytes, ack_first: bool = True) -> None:
        self.good_token = good_token
        self.ack_first = ack_first
        self.writes: list[bytes] = []
        self._cb = None

    async def start_notify(self, char, callback):
        assert char == READ_CHAR_UUID
        self._cb = callback

    async def stop_notify(self, char):
        pass

    def _push(self, data: bytes) -> None:
        asyncio.get_running_loop().call_soon(self._cb, None, bytearray(data))

    async def write_gatt_char(self, char, data, response=True):
        assert char == WRITE_CHAR_UUID
        self.writes.append(bytes(data))
        if data == ID_PACKET:
            if self.ack_first:
                self._push(bytes([ACK]))
            for packet in encode_json_packets(DEVICE_LIST):
                self._push(packet)
        elif len(data) == 16:
            reply = DASHBOARD if data == self.good_token else {"as": 1}
            for packet in encode_json_packets(reply):
                self._push(packet)
            self._push(bytes([MARCO]))


@pytest.mark.parametrize("ack_first", [True, False])
async def test_authenticates_and_collects_state(ack_first):
    valve = FakeValve(TOKEN, ack_first)
    seen = []
    session = SignatureSession(valve, TOKEN, lambda state: seen.append(dict(state)))
    await session.start()
    await session.wait_authenticated(2)
    await asyncio.sleep(0.05)
    assert session.state["dtgr"] == 32925 and session.state["dlvt"] == 1
    assert seen and seen[-1]["dwu"] == 1900
    assert valve.writes[0] == ID_PACKET
    assert TOKEN in valve.writes
    assert bytes([POLO]) in valve.writes  # answered the keep-alive
    await session.close()
    assert valve.writes[-1] == RESET_COMMAND


async def test_wrong_token_raises_invalid_auth():
    valve = FakeValve(b"\x00" * 16)
    session = SignatureSession(valve, TOKEN)
    await session.start()
    with pytest.raises(InvalidAuth):
        await session.wait_authenticated(2)
    await session.close()


async def test_silent_valve_times_out():
    class Silent(FakeValve):
        async def write_gatt_char(self, char, data, response=True):
            self.writes.append(bytes(data))

    session = SignatureSession(Silent(TOKEN), TOKEN)
    await session.start()
    with pytest.raises(TimeoutError):
        await session.wait_authenticated(0.2)
    await session.close()


def test_token_must_be_16_bytes():
    with pytest.raises(ValueError):
        SignatureSession(FakeValve(TOKEN), b"short")

