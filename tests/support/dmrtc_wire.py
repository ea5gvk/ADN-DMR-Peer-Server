# ADN DMR Peer Server - tests support: dmrtc DMRT wire mirror
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

"""What a Tier III site (qradiolink/dmrtc ``NetworkSignalling``) puts on the wire, and how it reads ours.

A line-by-line Python port of ``src/networksignalling.cpp`` (builders
``create*Message`` and parsers ``parse*Message`` / ``validateNetMessage``), so
the server's DMRT codec is tested against the client's own byte layout rather
than against itself. The UUID is a parameter here (dmrtc draws a random one).
Parsers return ``None`` where dmrtc returns false.
"""

from __future__ import annotations


def _addr(value: int) -> bytes:
    return bytes(((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))


def _get_addr(msg: bytes, offset: int) -> int:
    return (msg[offset] << 16) | (msg[offset + 1] << 8) | msg[offset + 2]


def _ts(ts: int) -> bytes:
    return int(ts).to_bytes(8, "little")   # memcpy of a uint64_t on a little-endian host


# --- builders (client -> network) ---------------------------------------

def create_registration_message(dmr_id: int, ts: int) -> bytes:
    return b"DMRT" + bytes((0x01,)) + _ts(ts) + bytes((1,)) + _addr(dmr_id)              # 17 bytes


def create_deregistration_message(dmr_id: int, ts: int) -> bytes:
    return b"DMRT" + bytes((0x02,)) + _ts(ts) + bytes((1,)) + _addr(dmr_id)              # 17 bytes


def _group_message(opcode: int, tgs: list[int]) -> bytes | None:
    if len(tgs) < 1:
        return None
    tg_size = min(len(tgs), 64)
    return b"DMRT" + bytes((opcode, tg_size)) + b"".join(_addr(tg) for tg in tgs[:tg_size])   # 6 + 3n


def create_group_subscription_message(tgs: list[int]) -> bytes | None:
    return _group_message(0x03, tgs)


def create_group_unsubscription_message(tgs: list[int]) -> bytes | None:
    return _group_message(0x04, tgs)


def create_udt_transfer_message(
    src_id: int, dst_id: int, payload: bytes, fmt: int, group: bool, uuid: bytes,
) -> bytes | None:
    if len(payload) > 46:
        return None
    type_byte = (0x80 if group else 0) | (0 if group else 0x40) | (fmt & 0x0F)
    return (
        b"DMRT" + bytes((0x05,)) + uuid + bytes((type_byte, len(payload)))
        + _addr(src_id) + _addr(dst_id) + payload                                          # 29 + n
    )


def create_udt_accept_message(src_id: int, dst_id: int, uuid: bytes) -> bytes:
    return b"DMRT" + bytes((0x06,)) + uuid + _addr(src_id) + _addr(dst_id)               # 27 bytes


def create_private_call_setup_message(src_id: int, dst_id: int, service_options: int, uuid: bytes) -> bytes:
    return b"DMRT" + bytes((0x07,)) + uuid + bytes((service_options,)) + _addr(src_id) + _addr(dst_id)   # 28


def create_private_call_reply_message(src_id: int, dst_id: int, uuid: bytes, accept: bool) -> bytes:
    return b"DMRT" + bytes((0x08 if accept else 0x09,)) + uuid + _addr(src_id) + _addr(dst_id)        # 27


def create_status_transfer_message(src_id: int, dst_id: int, status: int, group: bool, uuid: bytes) -> bytes:
    status_byte = (0x80 if group else 0) | (status & 0x7F)
    return b"DMRT" + bytes((0x0A,)) + uuid + bytes((status_byte,)) + _addr(src_id) + _addr(dst_id)  # 28


# --- parsers (network -> client) ----------------------------------------

def validate_net_message(msg: bytes) -> bool:
    if len(msg) < 5:
        return False
    if msg[:4] != b"DMRT":
        return False
    return (msg[4] & 0x40) == 0x40


def parse_registration_confirmation_message(msg: bytes) -> tuple[int, bool] | None:
    if len(msg) < 18:
        return None
    accept = bool(msg[13] & 0x01)
    return _get_addr(msg, 15), accept


def parse_deregistration_confirmation_message(msg: bytes) -> int | None:
    if len(msg) < 17:
        return None
    return _get_addr(msg, 14)


def parse_subscription_confirmation_message(msg: bytes) -> list[int] | None:
    """Also ``parseUnSubscriptionConfirmationMessage`` (identical). dmrtc does not bound the
    loop by the size, so a count larger than the TGs present raises IndexError here."""
    if len(msg) < 9:
        return None
    return [_get_addr(msg, 6 + 3 * i) for i in range(msg[5])]


def parse_udt_transfer_message(msg: bytes) -> dict | None:
    if len(msg) < 29:
        return None
    payload_size = msg[22]
    if len(msg) < 29 + payload_size:
        return None
    return {
        "uuid": msg[5:21],
        "group": (msg[21] >> 7) == 0x01,
        "format": msg[21] & 0x7F,
        "src": _get_addr(msg, 23),
        "dst": _get_addr(msg, 26),
        "message": msg[29:29 + payload_size],
    }


def parse_udt_accept_message(msg: bytes) -> tuple[int, int, bytes] | None:
    if len(msg) < 27:
        return None
    return _get_addr(msg, 21), _get_addr(msg, 24), msg[5:21]
