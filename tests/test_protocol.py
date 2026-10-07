# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
import json

import pytest

from chandler_signature.models import (
    battery_volts,
    firmware_version,
    is_metered_softener,
    salt_percent,
    scaled,
    serial_number,
    valve_type_name,
)
from chandler_signature.protocol import (
    ACK,
    MARCO,
    NAK,
    POLO,
    TIMEOUT_ACK,
    TIMEOUT_QUERY,
    PacketReader,
    crc16,
    encode_json_packets,
    encode_packet,
    packet_crc_ok,
    parse_token,
)

# The "authentication failed" reply printed in Chandler's guide: {"as":1}
GUIDE_PACKET = bytes([0xC0, 0x7B, 0x22, 0x61, 0x73, 0x22, 0x3A, 0x31, 0x7D, 0x9F, 0x38])


def test_crc_matches_guide_example():
    assert packet_crc_ok(GUIDE_PACKET)
    assert not packet_crc_ok(GUIDE_PACKET[:-1] + b"\x00")
    assert not packet_crc_ok(b"\x01\x02")


def test_encode_roundtrip():
    packet = encode_packet(0xC0, b'{"as":1}')
    assert packet == GUIDE_PACKET
    assert crc16(b"") == 0xFFFF


def test_single_json_packet_is_acked():
    events, replies = PacketReader().feed(GUIDE_PACKET)
    assert [e.kind for e in events] == ["json"]
    assert events[0].data == {"as": 1}
    assert replies == [bytes([ACK])]


def test_multi_packet_message_is_reassembled():
    obj = {"ggd": list(range(400)), "dh": 11}
    packets = encode_json_packets(obj, mtu=100)
    assert len(packets) > 1
    reader = PacketReader()
    seen = []
    for packet in packets:
        events, replies = reader.feed(packet)
        assert replies == [bytes([ACK])]  # every fragment is acknowledged
        seen.extend(events)
    assert len(seen) == 1 and seen[0].data == obj


def test_bad_crc_gets_nak_and_resets_buffer():
    broken = bytearray(GUIDE_PACKET)
    broken[3] ^= 0xFF
    events, replies = PacketReader().feed(bytes(broken))
    assert [e.kind for e in events] == ["bad_crc"]
    assert replies == [bytes([NAK])]


def test_invalid_json_is_reported_but_acked():
    packet = encode_packet(0xC0, b"{not json}")
    events, replies = PacketReader().feed(packet)
    assert [e.kind for e in events] == ["bad_json"]
    assert replies == [bytes([ACK])]


@pytest.mark.parametrize(
    ("byte", "kind", "reply"),
    [
        (0xCC, "ack", None),
        (0xC8, "nak", None),
        (MARCO, "marco", POLO),
        (POLO, "polo", None),
        (TIMEOUT_QUERY, "timeout_query", TIMEOUT_ACK),
        (TIMEOUT_ACK, "timeout_ack", None),
    ],
)
def test_status_packets(byte, kind, reply):
    events, replies = PacketReader().feed(bytes([byte]))
    assert events[0].kind == kind
    assert replies == ([bytes([reply])] if reply is not None else [])


def test_parse_token():
    expected = bytes.fromhex("8dffdfbd40cc4b868df81d48463b20f1")
    assert parse_token("8dffdfbd-40cc-4b86-8df8-1d48463b20f1") == expected
    assert parse_token(" 8DFFDFBD40CC4B868DF81D48463B20F1 \n") == expected
    with pytest.raises(ValueError):
        parse_token("not a token")
    with pytest.raises(ValueError):
        parse_token("abcd")


SOFTENER = json.loads(
    '{"dlsa":255,"dlsb":4096,"dlf":618,"dlvt":1,"dlvs":3,"dbl":0,'
    '"dtgr":32925,"dwu":1900,"dpfd":342,"dbtr":2130,"dbts":249}'
)


def test_accumulate_daily_counter():
    from chandler_signature.models import accumulate_daily

    assert accumulate_daily(0.0, None, 19.0) == 0.0  # first reading is only a baseline
    assert accumulate_daily(0.0, 19.0, 25.5) == 6.5  # normal increase
    assert accumulate_daily(6.5, 25.5, 25.5) == 6.5  # no change
    assert accumulate_daily(6.5, 25.5, 3.0) == 9.5  # daily reset: new value is all new usage
    assert accumulate_daily(9.5, 3.0, 0.0) == 9.5  # reset to zero adds nothing


def test_models():
    assert firmware_version(SOFTENER) == "C6.18"
    assert firmware_version({}) is None
    assert serial_number(SOFTENER) == "FF1000"
    assert serial_number({"dlsa": 1}) is None
    assert valve_type_name(SOFTENER) == "Metered Softener"
    assert valve_type_name({"dlvt": 12}) == "Aeration Filter"
    assert valve_type_name({}) == "Water Treatment Valve"
    assert is_metered_softener(SOFTENER) and not is_metered_softener({"dlvt": 12})
    assert scaled(SOFTENER, "dtgr", 100) == 329.25
    assert scaled(SOFTENER, "missing", 100) is None
    assert battery_volts(SOFTENER) is None  # 0 means "no battery reading"
    assert battery_volts({"dbl": 8184}) == 8.184
    assert salt_percent(SOFTENER) == 85.5
    assert salt_percent({"dbtr": 10, "dbts": 0}) is None

