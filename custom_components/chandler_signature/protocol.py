# Written by Claude Sonnet 5.5 (claude-sonnet-5-5), effort level: medium.
"""Packet layer for the Chandler Systems Signature Bluetooth API (firmware C6.13+).

Pure Python: no Home Assistant or Bluetooth imports, so it can be unit tested offline.

Wire format (see Chandler's published "Signature Bluetooth API Guide"):

* Status packet: a single header byte (ACK/NAK, keep-alive, timeout).
* Data packet: one header byte, JSON bytes, then a two byte CRC-16 (low byte first)
  computed over the header and JSON. Messages larger than the MTU are split into a
  FIRST ... LAST sequence of such packets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SERVICE_UUID = "a725458c-bee1-4d2e-9555-edf5a8082303"
READ_CHAR_UUID = "a725458c-bee2-4d2e-9555-edf5a8082303"  # valve -> client (notify)
WRITE_CHAR_UUID = "a725458c-bee3-4d2e-9555-edf5a8082303"  # client -> valve
MANUFACTURER_ID = 1850

# Header flag bits.
FIRST = 0x80
LAST = 0x40
KEEPALIVE = 0x20
KEEPALIVE_TYPE = 0x10
ACKNAK = 0x08
ACKNAK_TYPE = 0x04
TIMEOUT = 0x02
TIMEOUT_TYPE = 0x01
SINGLE = FIRST | LAST

ACK = SINGLE | ACKNAK | ACKNAK_TYPE  # 0xCC
NAK = SINGLE | ACKNAK  # 0xC8
MARCO = SINGLE | KEEPALIVE  # 0xE0 (valve asks "are you there?")
POLO = SINGLE | KEEPALIVE | KEEPALIVE_TYPE  # 0xF0 (our reply)
TIMEOUT_QUERY = SINGLE | TIMEOUT  # 0xC2
TIMEOUT_ACK = SINGLE | TIMEOUT | TIMEOUT_TYPE  # 0xC3

ID_PACKET = bytes([0xEA])  # sent first; puts the valve in "expect authentication" state
RESET_COMMAND = b"R"  # asks the valve to disconnect gracefully

HEADER_SIZE = 1
CRC_SIZE = 2
MIN_DATA_PACKET = HEADER_SIZE + CRC_SIZE + 2  # header + '{}' + crc
MAX_PACKET = 251  # the valve's maximum MTU
_MAX_BUFFER = 256 * 1024


def crc16(buf: bytes, seed: int = 0xFFFF) -> int:
    """CRC-16 used by the valve (reflected CCITT, as in Zephyr's crc16_ccitt).

    Follows the Zephyr/Intel implementation, Copyright (c) 2017 Intel Corporation,
    Apache-2.0 (see NOTICE).
    """
    crc = seed
    for byte in buf:
        crc = ((crc >> 8) | (crc << 8)) & 0xFFFF
        crc ^= byte & 0xFF
        crc ^= (crc & 0xFF) >> 4
        crc ^= (crc << 12) & 0xFFFF
        crc ^= ((crc & 0xFF) << 5) & 0xFFFF
    return crc & 0xFFFF


def packet_crc_ok(packet: bytes) -> bool:
    """Return True if the trailing CRC (low byte first) matches the packet."""
    if len(packet) < MIN_DATA_PACKET:
        return False
    expected = (packet[-1] << 8) | packet[-2]
    return crc16(packet[:-2]) == expected


def encode_packet(header: int, payload: bytes, *, to_valve: bool = False) -> bytes:
    """Build one data packet: header, payload, CRC.

    The CRC byte order differs by direction, found by testing against a real valve (the
    published guide only shows the receive side): packets the valve sends carry the CRC low
    byte first, but the valve only accepts packets sent to it with the CRC **high byte
    first** (it answers anything else with a NAK).
    """
    body = bytes([header]) + payload
    crc = crc16(body)
    tail = bytes([crc >> 8, crc & 0xFF]) if to_valve else bytes([crc & 0xFF, crc >> 8])
    return body + tail


def encode_json_packets(obj: Any, mtu: int = MAX_PACKET, *, to_valve: bool = False) -> list[bytes]:
    """Encode a JSON object into one or more packets no larger than the MTU."""
    data = json.dumps(obj, separators=(",", ":")).encode()
    chunk = mtu - HEADER_SIZE - CRC_SIZE
    parts = [data[i : i + chunk] for i in range(0, len(data), chunk)] or [b""]
    packets: list[bytes] = []
    for index, part in enumerate(parts):
        header = 0
        if index == 0:
            header |= FIRST
        if index == len(parts) - 1:
            header |= LAST
        packets.append(encode_packet(header, part, to_valve=to_valve))
    return packets


@dataclass(frozen=True, slots=True)
class Event:
    """Something decoded from the valve."""

    kind: str  # ack, nak, marco, polo, timeout_query, timeout_ack, status, json, bad_crc, bad_json
    data: Any = None


class PacketReader:
    """Reassemble notifications into events and work out the replies to send."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, packet: bytes) -> tuple[list[Event], list[bytes]]:
        """Process one notification. Returns (events, replies to write, in order)."""
        if len(packet) == 1:
            return self._status(packet[0])
        if len(packet) < MIN_DATA_PACKET or not packet_crc_ok(packet):
            self._buffer.clear()
            return [Event("bad_crc", bytes(packet))], [bytes([NAK])]

        header, body = packet[0], packet[1:-2]
        if header & FIRST:
            self._buffer.clear()
        self._buffer.extend(body)
        if len(self._buffer) > _MAX_BUFFER:
            self._buffer.clear()
            return [Event("bad_json", None)], [bytes([NAK])]

        replies = [bytes([ACK])]
        if not header & LAST:
            return [], replies

        text = bytes(self._buffer).decode("utf-8", errors="replace")
        self._buffer.clear()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return [Event("bad_json", text)], replies
        if not isinstance(payload, dict):
            return [Event("bad_json", text)], replies
        return [Event("json", payload)], replies

    @staticmethod
    def _status(header: int) -> tuple[list[Event], list[bytes]]:
        if header & ACKNAK:
            return [Event("ack" if header & ACKNAK_TYPE else "nak")], []
        if header & KEEPALIVE:
            if header & KEEPALIVE_TYPE:
                return [Event("polo")], []
            return [Event("marco")], [bytes([POLO])]
        if header & TIMEOUT:
            if header & TIMEOUT_TYPE:
                return [Event("timeout_ack")], []
            return [Event("timeout_query")], [bytes([TIMEOUT_ACK])]
        return [Event("status", header)], []


def parse_token(text: str) -> bytes:
    """Convert the API token (a UUID, dashes optional) into its 16 raw bytes."""
    cleaned = text.strip().replace("-", "")
    raw = bytes.fromhex(cleaned)  # raises ValueError on non-hex input
    if len(raw) != 16:
        raise ValueError("The API token must be a 16 byte UUID")
    return raw

