# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Exercise SignatureSession against a fake valve that follows the documented flow."""

import asyncio

import pytest

from chandler_signature.protocol import (
    ACK,
    ID_PACKET,
    MARCO,
    NAK,
    POLO,
    packet_crc_ok,
    READ_CHAR_UUID,
    RESET_COMMAND,
    WRITE_CHAR_UUID,
    encode_json_packets,
)
from chandler_signature.session import InvalidAuth, SessionClosed, SignatureSession

TOKEN = bytes.fromhex("8dffdfbd40cc4b868df81d48463b20f1")
DEVICE_LIST = {"dlf": 618, "dlvt": 1, "dlsa": 1, "dlsb": 2}
DASHBOARD = {"as": 2, "dwu": 1900, "dtgr": 32925}


def swap_crc(packet: bytes) -> bytes:
    """Swap the two CRC bytes (client->valve packets are high byte first)."""
    return packet[:-2] + bytes([packet[-1], packet[-2]])


class FakeValve:
    """Minimal Bleak-like client that behaves like a valve."""

    def __init__(self, good_token: bytes, ack_first: bool = True) -> None:
        self.good_token = good_token
        self.ack_first = ack_first
        self.ack_writes = True
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
        elif len(data) >= 5 and self.ack_writes:
            # A real valve only accepts packets sent to it with the CRC high byte first.
            if packet_crc_ok(swap_crc(bytes(data))):
                self._push(bytes([ACK]))
            else:
                self._push(bytes([NAK]))


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


async def test_send_json_writes_valid_packets():
    from chandler_signature.protocol import PacketReader

    valve = FakeValve(TOKEN)
    session = SignatureSession(valve, TOKEN)
    with pytest.raises(SessionClosed):
        await session.send_json({"dh": 1, "dm": 2})  # refused before authentication
    await session.start()
    await session.wait_authenticated(2)
    before = len(valve.writes)

    await session.send_json({"dh": 21, "dm": 53})  # 17 bytes of JSON: one 20-byte write
    sent = [w for w in valve.writes[before:] if len(w) > 1 and w[0] != ACK]
    assert len(sent) == 1 and len(sent[0]) == 20
    sent = valve.writes[before:]
    assert len(sent) == 1 and len(sent[0]) == 20
    events, _ = PacketReader().feed(swap_crc(sent[0]))
    assert events[0].data == {"dh": 21, "dm": 53}

    before = len(valve.writes)
    await session.send_json({"dh": 21, "dm": 53, "ds": 7, "x": "y" * 30}, max_packet=20)
    chunks = [c for c in valve.writes[before:] if len(c) > 1]
    assert len(chunks) > 1 and all(len(c) <= 20 for c in chunks)
    reader, result = PacketReader(), []
    for chunk in chunks:
        events, _ = reader.feed(swap_crc(chunk))
        result.extend(e.data for e in events)
    assert result == [{"dh": 21, "dm": 53, "ds": 7, "x": "y" * 30}]
    await session.close()


async def test_send_json_waits_for_each_ack_and_times_out():
    valve = FakeValve(TOKEN)
    session = SignatureSession(valve, TOKEN)
    await session.start()
    await session.wait_authenticated(2)
    valve.ack_writes = False  # a valve that never acknowledges
    with pytest.raises(SessionClosed):
        await session.send_json({"dh": 1, "dm": 2}, ack_timeout=0.2)
    await session.close()


async def test_wrong_crc_order_is_rejected_by_the_valve():
    from chandler_signature.protocol import encode_packet

    valve = FakeValve(TOKEN)
    session = SignatureSession(valve, TOKEN)
    await session.start()
    await session.wait_authenticated(2)
    # What the first version sent: CRC low byte first. The real valve NAKs it.
    await valve.write_gatt_char(WRITE_CHAR_UUID, encode_packet(0xC0, b'{"dh":1,"dm":2}'))
    await asyncio.sleep(0.05)
    assert session._last_reply_nak
    await session.close()


def test_token_must_be_16_bytes():
    with pytest.raises(ValueError):
        SignatureSession(FakeValve(TOKEN), b"short")

