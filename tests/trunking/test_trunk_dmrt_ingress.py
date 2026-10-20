# ADN DMR Peer Server - tests DMR Tier III DMRT through the HBP MASTER
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

"""DMRT signalling over UDP: a site's message in, the answer written to the right site's address.

The protocols, the trunking use cases and the registry are wired as bootstrap
wires them (``create_trunking_use_cases``); messages are built and answers read
with the dmrtc mirror (``tests/support/dmrtc_wire.py``).
"""

from __future__ import annotations

import time

import pytest

from adn_server.domain import bytes_3
from adn_server.infrastructure.hbp_constants import DMRD, DTCCL
from tests.harness.deterministic import DeterministicScenario, PacketSpec
from tests.support import dmrtc_wire as dmrtc
from tests.support.trunk_site_stack import T1, T1_ADDR, T2, T2_ADDR, T3, T3_ADDR, TrunkStack

pytestmark = pytest.mark.integration

MS = 2140501
UUID_A = bytes(range(0xA0, 0xB0))


def _two_sites(**kwargs) -> TrunkStack:
    s = TrunkStack(**kwargs)
    s.login("TRUNK", T1, T1_ADDR)
    s.login("TRUNK", T2, T2_ADDR)
    return s


def test_registration_is_confirmed_to_the_sites_address() -> None:
    s = _two_sites()
    s.send("TRUNK", dmrtc.create_registration_message(MS, int(time.time())), T1_ADDR)

    replies = s.to("TRUNK", T1_ADDR)
    assert len(replies) == 1 and replies[0][4] == 0x41
    assert dmrtc.validate_net_message(replies[0])
    assert dmrtc.parse_registration_confirmation_message(replies[0]) == (MS, True)
    assert s.to("TRUNK", T2_ADDR) == []
    assert s.registry.location(MS) == ("TRUNK", T1)
    system, slot, _t, peer_id = s.config["_SUB_MAP"][bytes_3(MS)]
    assert (system, slot, peer_id) == ("TRUNK", 1, T1)


def test_no_sub_map_entry_when_the_system_does_not_learn() -> None:
    s = TrunkStack(system={"SUB_MAP_LEARN": False})
    s.login("TRUNK", T1, T1_ADDR)
    s.send("TRUNK", dmrtc.create_registration_message(MS, int(time.time())), T1_ADDR)
    assert s.registry.location(MS) == ("TRUNK", T1)
    assert s.config["_SUB_MAP"] == {}


def test_subscription_is_confirmed_and_released_when_the_site_closes() -> None:
    s = _two_sites()
    s.send("TRUNK", dmrtc.create_group_subscription_message([91, 214]), T1_ADDR)
    assert dmrtc.parse_subscription_confirmation_message(s.to("TRUNK", T1_ADDR)[-1]) == [91, 214]
    assert s.registry.peer_tgs("TRUNK", T1) == [91, 214]

    s.send("TRUNK", DTCCL + T1, T1_ADDR)

    assert s.registry.peer_tgs("TRUNK", T1) == [] and s.registry.subscribers("TRUNK", 91) == []


def test_roaming_across_trunking_systems_deregisters_at_the_old_site() -> None:
    s = TrunkStack(trunking=("TRUNK", "TRUNK-B"))
    s.login("TRUNK", T1, T1_ADDR)
    s.login("TRUNK-B", T3, T3_ADDR)
    s.send("TRUNK", dmrtc.create_registration_message(MS, int(time.time())), T1_ADDR)
    s.transports["TRUNK"].clear()

    s.send("TRUNK-B", dmrtc.create_registration_message(MS, int(time.time())), T3_ADDR)

    assert dmrtc.parse_registration_confirmation_message(s.to("TRUNK-B", T3_ADDR)[-1]) == (MS, True)
    old = s.to("TRUNK", T1_ADDR)
    assert len(old) == 1 and old[0][4] == 0x42
    assert dmrtc.parse_deregistration_confirmation_message(old[0]) == MS
    assert s.registry.location(MS) == ("TRUNK-B", T3)


def test_group_udt_reaches_the_other_subscribed_site() -> None:
    s = _two_sites()
    s.send("TRUNK", dmrtc.create_group_subscription_message([91]), T1_ADDR)
    s.send("TRUNK", dmrtc.create_group_subscription_message([91]), T2_ADDR)
    s.transports["TRUNK"].clear()

    s.send("TRUNK", dmrtc.create_udt_transfer_message(MS, 91, b"hello", 4, True, UUID_A), T1_ADDR)

    assert s.to("TRUNK", T1_ADDR) == []
    (udt,) = s.to("TRUNK", T2_ADDR)
    assert udt[4] == 0x45 and dmrtc.parse_udt_transfer_message(udt)["message"] == b"hello"

    s.send("TRUNK", dmrtc.create_udt_accept_message(91, MS, UUID_A), T2_ADDR)
    (accept,) = s.to("TRUNK", T1_ADDR)
    assert accept[4] == 0x46 and dmrtc.parse_udt_accept_message(accept)[2] == UUID_A


def test_dmrt_from_an_unknown_address_is_ignored() -> None:
    s = _two_sites()
    s.send("TRUNK", dmrtc.create_registration_message(MS, int(time.time())), ("10.9.9.9", 1))
    assert s.transports["TRUNK"].sent == [] and s.registry.location(MS) is None


def test_dmrt_before_the_login_completes_is_ignored() -> None:
    s = TrunkStack()
    s.send("TRUNK", b"DTCL" + T1, T1_ADDR)                                 # CHALLENGE_SENT
    s.transports["TRUNK"].clear()
    s.send("TRUNK", dmrtc.create_registration_message(MS, int(time.time())), T1_ADDR)
    assert s.transports["TRUNK"].sent == [] and s.registry.location(MS) is None


@pytest.mark.parametrize("payload", [b"DMRT", b"DMRT\x03" + b"\x00" * 251])
def test_dmrt_of_a_bad_size_is_ignored(payload: bytes) -> None:
    s = _two_sites()
    s.send("TRUNK", payload, T1_ADDR)
    assert s.transports["TRUNK"].sent == []


def test_send_trunking_message_guards() -> None:
    s = TrunkStack(classic=("MASTER-A",))
    s.login("TRUNK", T1, T1_ADDR)
    p = s.protocols["TRUNK"]
    ok = b"DMRT\x42" + b"\x00" * 8 + b"\x01" + bytes_3(MS)                 # 0x42, 17 bytes
    assert p.send_trunking_message(T1, ok) is True
    assert s.to("TRUNK", T1_ADDR) == [ok]
    assert p.send_trunking_message(T2, ok) is False                          # not logged in
    assert p.send_trunking_message(T1, b"DMRT\x02" + ok[5:]) is False       # not a network message
    assert p.send_trunking_message(T1, b"DMRT\xc1") is False                # local-only (MasterLogin)
    assert p.send_trunking_message(T1, b"DMRT\x45" + b"\x00" * 251) is False  # > 255 bytes
    assert p.send_trunking_message(T1, b"DMRD\x41" + b"\x00" * 50) is False
    classic = s.protocols["MASTER-A"]
    classic._peers[T2] = {"CONNECTION": "YES", "SOCKADDR": T2_ADDR}
    assert classic.send_trunking_message(T2, ok) is False                    # a hotspot never gets DMRT
    assert s.to("TRUNK", T1_ADDR) == [ok] and s.transports["MASTER-A"].sent == []


def test_dmrd_from_a_site_still_takes_the_classic_path() -> None:
    """Voice from a site reaches routing unchanged until the trunking DMRD paths exist."""
    received: list[tuple] = []
    s = TrunkStack(dmrd_received=lambda *args, **kw: received.append(args))
    s.login("TRUNK", T1, T1_ADDR)
    spec = PacketSpec(peer_id=2149001, rf_src=2140501, dst_id=91, stream_id=0x0A0B0C0D)
    head = DeterministicScenario.voice_head_spec(spec).data()
    s.send("TRUNK", head, T1_ADDR)
    s.send("TRUNK", head + UUID_A, T1_ADDR)                                 # 71 bytes, as dmrtc sends it
    assert [(args[0], args[1], args[10][:4]) for args in received] == [("TRUNK", T1, DMRD)] * 2
