# ADN DMR Peer Server - DMR Tier III trunking wire format
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
###############################################################################
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
###############################################################################

"""Homebrew DMR trunking protocol v0.6 (DL5ABM / YO8RZZ): opcodes and DMRT messages.

A Tier III site is a DMRGateway with ``TrunkingEnabled=1`` in front of dmrtc.
It logs in like a hotspot with every opcode renamed (``DTCL``/``DTCK``/
``DTCC``/``DTCO``/``DTCPING``), appends a 16-byte call UUID to every DMRD
(71 bytes) and talks ``DMRT`` signalling: MS registrations, talkgroup
subscriptions, UDT short data, private-call setup and status.

``DMRT`` layout: ``b"DMRT"``, byte 4 = opcode, then the opcode's fields.
Addresses are 24-bit big-endian, timestamps signed 64-bit little-endian unix
seconds, a message never exceeds 255 bytes. Opcode bit ``0x40`` marks a message
that comes *from* the network: a client sends ``0x01``..``0x0A`` and drops
anything without ``0x40``; ``0x80`` and up never leave the site.

Parsers validate lengths exactly as dmrtc (``networksignalling.cpp``) builds
them and answer ``None`` on anything else; builders produce what dmrtc's
parsers read.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import IntEnum

# Login / keepalive opcodes (the trunking names of RPTL, RPTK, ...).
DTCL = b"DTCL"
DTCK = b"DTCK"
DTCC = b"DTCC"
DTCCL = b"DTCCL"
DTCO = b"DTCO"
DTCG = b"DTCG"
DTCPING = b"DTCPING"
DTCP = b"DTCP"   # DTCPING as seen by a 4-char command switch
DTCACK = b"DTCACK"
DTCNAK = b"DTCNAK"
DTCPONG = b"DTCPONG"
DTCSBKN = b"DTCSBKN"
DMRT = b"DMRT"

TRUNK_DMRD_LEN = 71
"""DMRD from/to a trunking client: the classic 55 bytes plus the call UUID."""
CALL_UUID_LEN = 16

DMRT_MAX_LEN = 255
FROM_NETWORK = 0x40
LOCAL_ONLY = 0x80

MAX_REGISTRATION_IDS = 8
"""The 0x41 status bitfield is one byte: at most 8 addresses per message."""
MAX_SUBSCRIPTION_TGS = 64
MAX_UDT_PAYLOAD = 46

ECHO_TG_MIN = 9990
ECHO_TG_MAX = 9999


class DmrtOp(IntEnum):
    """Client-to-network DMRT opcodes; the network answer is ``op | FROM_NETWORK``."""

    REGISTRATION = 0x01
    DEREGISTRATION = 0x02
    GROUP_SUB = 0x03
    GROUP_UNSUB = 0x04
    UDT = 0x05
    UDT_ACCEPT = 0x06
    PC_SETUP = 0x07
    PC_ACCEPT = 0x08
    PC_REJECT = 0x09
    STATUS = 0x0A


@dataclass(frozen=True)
class DmrtRegistration:
    """0x01 registration / 0x02 de-registration: MS presence at the sending site."""

    op: DmrtOp
    timestamp: int
    ms_ids: tuple[int, ...]


@dataclass(frozen=True)
class DmrtSubscription:
    """0x03 subscription / 0x04 un-subscription: talkgroups the site wants (no timestamp)."""

    op: DmrtOp
    tgs: tuple[int, ...]


@dataclass(frozen=True)
class DmrtUdt:
    """0x05 UDT transfer (SMS, location, ...): bit 7 of ``udt_type`` = group."""

    op: DmrtOp
    uuid: bytes
    udt_type: int
    src: int
    dst: int
    payload: bytes

    @property
    def group(self) -> bool:
        return bool(self.udt_type & 0x80)


@dataclass(frozen=True)
class DmrtCallSetup:
    """0x07 private call setup."""

    op: DmrtOp
    uuid: bytes
    service_options: int
    src: int
    dst: int


@dataclass(frozen=True)
class DmrtReply:
    """0x06 UDT accept, 0x08 private call accept, 0x09 private call reject: answer to ``uuid``."""

    op: DmrtOp
    uuid: bytes
    src: int
    dst: int


@dataclass(frozen=True)
class DmrtStatus:
    """0x0A status transfer: bit 7 of ``status_byte`` = group, low 7 bits = status."""

    op: DmrtOp
    uuid: bytes
    status_byte: int
    src: int
    dst: int

    @property
    def group(self) -> bool:
        return bool(self.status_byte & 0x80)

    @property
    def status(self) -> int:
        return self.status_byte & 0x7F


DmrtMessage = DmrtRegistration | DmrtSubscription | DmrtUdt | DmrtCallSetup | DmrtReply | DmrtStatus


def _addr(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 3], "big")


def _addrs(data: bytes, offset: int, count: int) -> tuple[int, ...]:
    return tuple(_addr(data, offset + 3 * i) for i in range(count))


def _pack_addr(value: int) -> bytes:
    value = int(value)
    if not 0 <= value <= 0xFFFFFF:
        raise ValueError(f"DMRT address out of range: {value}")
    return value.to_bytes(3, "big")


def _pack_ts(ts: int | float) -> bytes:
    return int(ts).to_bytes(8, "little", signed=True)


def _parse_registration(op: DmrtOp, data: bytes) -> DmrtRegistration | None:
    # [5..12] timestamp, [13] count, [14..] 3 bytes per MS (17 bytes for one)
    if len(data) < 17:
        return None
    count = data[13]
    if not 1 <= count <= MAX_REGISTRATION_IDS or len(data) != 14 + 3 * count:
        return None
    ts = int.from_bytes(data[5:13], "little", signed=True)
    return DmrtRegistration(op, ts, _addrs(data, 14, count))


def _parse_subscription(op: DmrtOp, data: bytes) -> DmrtSubscription | None:
    # [5] count, [6..] 3 bytes per TG
    if len(data) < 9:
        return None
    count = data[5]
    if not 1 <= count <= MAX_SUBSCRIPTION_TGS or len(data) != 6 + 3 * count:
        return None
    return DmrtSubscription(op, _addrs(data, 6, count))


def _parse_udt(op: DmrtOp, data: bytes) -> DmrtUdt | None:
    # [5..20] UUID, [21] type, [22] payload size, [23..25] src, [26..28] dst, [29..] payload
    if len(data) < 29:
        return None
    size = data[22]
    if size > MAX_UDT_PAYLOAD or len(data) != 29 + size:
        return None
    return DmrtUdt(op, data[5:21], data[21], _addr(data, 23), _addr(data, 26), data[29:])


def _parse_reply(op: DmrtOp, data: bytes) -> DmrtReply | None:
    # [5..20] UUID, [21..23] src, [24..26] dst (27 bytes)
    if len(data) != 27:
        return None
    return DmrtReply(op, data[5:21], _addr(data, 21), _addr(data, 24))


def _parse_call_setup(op: DmrtOp, data: bytes) -> DmrtCallSetup | None:
    # [5..20] UUID, [21] service options, [22..24] src, [25..27] dst (28 bytes)
    if len(data) != 28:
        return None
    return DmrtCallSetup(op, data[5:21], data[21], _addr(data, 22), _addr(data, 25))


def _parse_status(op: DmrtOp, data: bytes) -> DmrtStatus | None:
    # [5..20] UUID, [21] group bit + 7-bit status, [22..24] src, [25..27] dst (28 bytes)
    if len(data) != 28:
        return None
    return DmrtStatus(op, data[5:21], data[21], _addr(data, 22), _addr(data, 25))


_PARSERS = {
    DmrtOp.REGISTRATION: _parse_registration,
    DmrtOp.DEREGISTRATION: _parse_registration,
    DmrtOp.GROUP_SUB: _parse_subscription,
    DmrtOp.GROUP_UNSUB: _parse_subscription,
    DmrtOp.UDT: _parse_udt,
    DmrtOp.UDT_ACCEPT: _parse_reply,
    DmrtOp.PC_SETUP: _parse_call_setup,
    DmrtOp.PC_ACCEPT: _parse_reply,
    DmrtOp.PC_REJECT: _parse_reply,
    DmrtOp.STATUS: _parse_status,
}


def parse_dmrt(payload: bytes) -> DmrtMessage | None:
    """Decode a client-to-network DMRT message; ``None`` for anything else (never raises).

    Network (``0x40``) and site-local (``0x80`` and up) opcodes are refused:
    a client never has a reason to send them to the server.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        return None
    data = bytes(payload)
    if len(data) < 5 or len(data) > DMRT_MAX_LEN or data[:4] != DMRT:
        return None
    try:
        op = DmrtOp(data[4])
    except ValueError:
        return None
    return _PARSERS[op](op, data)


def with_network_opcode(payload: bytes) -> bytes:
    """``payload`` with the from-network bit set on its opcode (forward a message verbatim)."""
    data = bytes(payload)
    if len(data) < 5 or data[:4] != DMRT:
        raise ValueError("not a DMRT message")
    return data[:4] + bytes((data[4] | FROM_NETWORK,)) + data[5:]


def build_registration_confirmation(statuses: list[bool], ids: list[int], ts: int | float) -> bytes:
    """0x41: ``[5..12]`` ts, ``[13]`` status bits (bit i = address i), ``[14]`` count, ``[15..]`` ids."""
    if not 1 <= len(ids) <= MAX_REGISTRATION_IDS or len(statuses) != len(ids):
        raise ValueError("registration confirmation needs 1..8 ids, one status each")
    bits = 0
    for i, accepted in enumerate(statuses):
        if accepted:
            bits |= 1 << i
    return (
        DMRT
        + bytes((DmrtOp.REGISTRATION | FROM_NETWORK,))
        + _pack_ts(ts)
        + bytes((bits, len(ids)))
        + b"".join(_pack_addr(ms) for ms in ids)
    )


def build_deregistration(ids: list[int], ts: int | float) -> bytes:
    """0x42 (de-registration from network): ``[5..12]`` ts, ``[13]`` count, ``[14..]`` ids."""
    if not 1 <= len(ids) <= MAX_REGISTRATION_IDS:
        raise ValueError("de-registration needs 1..8 ids")
    return (
        DMRT
        + bytes((DmrtOp.DEREGISTRATION | FROM_NETWORK,))
        + _pack_ts(ts)
        + bytes((len(ids),))
        + b"".join(_pack_addr(ms) for ms in ids)
    )


def build_group_confirmation(op: int, tgs: list[int]) -> bytes:
    """0x43 / 0x44: ``[5]`` count, ``[6..]`` TGs as asked, 0 for each one refused."""
    code = int(op) | FROM_NETWORK
    if code not in (DmrtOp.GROUP_SUB | FROM_NETWORK, DmrtOp.GROUP_UNSUB | FROM_NETWORK):
        raise ValueError(f"not a subscription opcode: {op:#x}")
    if not 1 <= len(tgs) <= MAX_SUBSCRIPTION_TGS:
        raise ValueError("subscription confirmation needs 1..64 talkgroups")
    return DMRT + bytes((code, len(tgs))) + b"".join(_pack_addr(tg) for tg in tgs)


def build_pc_reject(uuid: bytes, src: int, dst: int) -> bytes:
    """0x49 private call reject from the network (27 bytes)."""
    if len(uuid) != CALL_UUID_LEN:
        raise ValueError("UUID must be 16 bytes")
    return DMRT + bytes((DmrtOp.PC_REJECT | FROM_NETWORK,)) + bytes(uuid) + _pack_addr(src) + _pack_addr(dst)


def derive_stream_uuid(stream_id: bytes) -> bytes:
    """Deterministic RFC 4122 v4-shaped call UUID for a stream that has none.

    dmrtc derives its stream identity from the UUID of a 71-byte DMRD, so every
    frame of one call must carry the same UUID and two calls must differ.
    """
    digest = bytearray(hashlib.blake2b(b"ADN-TIER3" + bytes(stream_id), digest_size=CALL_UUID_LEN).digest())
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return bytes(digest)


def is_echo_tg(tg: int) -> bool:
    """9990..9999: parrot / echo, answered to the one site that keyed it."""
    return ECHO_TG_MIN <= int(tg) <= ECHO_TG_MAX


def trunk_tg_is_valid(tg: int) -> bool:
    """Whether a site may subscribe ``tg``: not 0..9, 4000, 5000, echo or above 24 bits."""
    tg = int(tg)
    if tg < 10 or tg > 0xFFFFFF:
        return False
    return tg not in (4000, 5000) and not is_echo_tg(tg)
