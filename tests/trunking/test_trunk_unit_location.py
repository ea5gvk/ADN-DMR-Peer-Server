# ADN DMR Peer Server - tests DMR Tier III: where a unit is
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

"""Private calls and unit data find a radio at its Tier III site, and only while it is there.

dmrtc sends a registration (0x01) once per radio and never again: not after the
gateway logs in again, and not when the radio walks off the repeater without
de-registering. So a registration outlives its site's re-login, gives way to a
later sighting of the radio anywhere else (SUB_MAP), and a SUB_MAP entry on a
trunking system ages like any other for the OpenBridge fan-out of unit data.
"""

from __future__ import annotations

import time

import pytest

from adn_server.domain import bytes_3, bytes_4
from adn_server.infrastructure.hbp_constants import DTCCL
from tests.harness.deterministic import DeterministicScenario, FakeClock, PacketSpec
from tests.support import dmrtc_wire as dmrtc
from tests.support.trunk_site_stack import T1, T1_ADDR, T2, T2_ADDR, TrunkRoutingStack, voice_frames

pytestmark = pytest.mark.integration

HS, HS_ADDR = bytes_4(2140001), ("10.0.0.1", 62031)
HC, HC_ADDR = bytes_4(2140002), ("10.0.0.2", 62031)
MS = 2149050           # a radio of the Tier III sites
HS_RADIO = 2140101     # the hotspot's radio


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    c = FakeClock()
    monkeypatch.setattr(time, "time", c.time)
    return c


def _stack() -> TrunkRoutingStack:
    s = TrunkRoutingStack(classic=("HOTSPOTS", "MASTER-C"), system={"GROUP_HANGTIME": 0})
    s.login_hotspot("HOTSPOTS", HS, HS_ADDR)
    s.login_hotspot("MASTER-C", HC, HC_ADDR)
    s.login("TRUNK", T1, T1_ADDR)
    s.login("TRUNK", T2, T2_ADDR)
    s.obp_sent = []
    s.protocols["OBP"].send_system = lambda packet, **kw: s.obp_sent.append(packet)
    return s


def _register(s: TrunkRoutingStack, addr: tuple[str, int], ms: int) -> None:
    s.send("TRUNK", dmrtc.create_registration_message(ms, int(time.time())), addr)
    s.transports["TRUNK"].clear()


def _private_call(stream_id: int) -> list[bytes]:
    base = PacketSpec(peer_id=2140001, rf_src=HS_RADIO, dst_id=MS, slot=2, stream_id=stream_id, call_type="unit")
    return [DeterministicScenario.unit_voice_head_spec(base).data()] + [
        DeterministicScenario.unit_voice_burst_spec(base, seq=n, dtype_vseq=n).data() for n in range(1, 5)
    ]


def _unit_data(stream_id: int = 0x0D0D0D0D) -> bytes:
    base = PacketSpec(peer_id=2140001, rf_src=HS_RADIO, dst_id=MS, slot=2, stream_id=stream_id, call_type="unit")
    return DeterministicScenario.unit_data_header_spec(base, dtype_vseq=6).data()


def _send(s: TrunkRoutingStack, clock: FakeClock, system: str, frames: list[bytes], addr: tuple[str, int]) -> None:
    for frame in frames:
        clock.advance(0.03)
        s.send(system, frame, addr)


def _clear(s: TrunkRoutingStack) -> None:
    for transport in s.transports.values():
        transport.clear()
    s.obp_sent.clear()


def test_registrations_survive_the_sites_relogin(clock) -> None:
    """dmrtc re-subscribes its TGs on MasterLogin but never registers its radios again."""
    s = _stack()
    _register(s, T1_ADDR, MS)
    clock.advance(30)
    s.login("TRUNK", T1, T1_ADDR)  # timed out / DTCNAK: DMRGateway logs in again
    s.send("TRUNK", dmrtc.create_group_subscription_message([214]), T1_ADDR)
    _clear(s)
    clock.advance(30)

    assert s.registry.location(MS) == ("TRUNK", T1)
    _send(s, clock, "HOTSPOTS", _private_call(0x0C0C0C0C), HS_ADDR)
    assert len(s.dmrd_to("TRUNK", T1_ADDR)) == 5
    s.send("TRUNK", dmrtc.create_private_call_setup_message(2149060, MS, 0, bytes(range(16))), T2_ADDR)
    assert s.to("TRUNK", T1_ADDR)[-1][4] == 0x47  # the setup reaches the site, no 0x49 back to T2


def test_a_later_sighting_on_a_hotspot_wins_over_the_registration(clock) -> None:
    """The radio left the repeater without de-registering and keyed up on another hotspot."""
    s = _stack()
    _register(s, T1_ADDR, MS)
    clock.advance(60)
    heard = voice_frames(PacketSpec(peer_id=2140002, rf_src=MS, dst_id=214, slot=2, stream_id=0x1111))
    _send(s, clock, "MASTER-C", heard, HC_ADDR)
    assert s.config["_SUB_MAP"][bytes_3(MS)][0] == "MASTER-C"
    _clear(s)
    clock.advance(10)

    _send(s, clock, "HOTSPOTS", _private_call(0x0E0E0E0E), HS_ADDR)
    assert len(s.dmrd_to("MASTER-C", HC_ADDR)) == 5
    assert s.dmrd_to("TRUNK", T1_ADDR) == []

    _clear(s)
    clock.advance(10)
    s.send("HOTSPOTS", _unit_data(), HS_ADDR)
    assert s.dmrd_to("MASTER-C", HC_ADDR)
    assert s.dmrd_to("TRUNK", T1_ADDR) == [] and s.obp_sent == []  # served here, as a fresh hotspot entry


def test_the_registration_wins_over_an_older_sighting_elsewhere(clock) -> None:
    s = _stack()
    s.config["_SUB_MAP"][bytes_3(MS)] = ("MASTER-C", 2, time.time(), HC)
    clock.advance(60)
    _register(s, T1_ADDR, MS)
    clock.advance(10)

    _send(s, clock, "HOTSPOTS", _private_call(0x0E0E0E0E), HS_ADDR)
    assert len(s.dmrd_to("TRUNK", T1_ADDR)) == 5
    assert s.dmrd_to("MASTER-C", HC_ADDR) == []


def test_unit_data_to_a_radio_registered_at_a_site_is_not_fanned_out_however_old(clock) -> None:
    s = _stack()
    _register(s, T1_ADDR, MS)
    _clear(s)
    clock.advance(3600)
    s.send("HOTSPOTS", _unit_data(), HS_ADDR)

    assert len(s.dmrd_to("TRUNK", T1_ADDR)) == 1
    assert s.obp_sent == []


def test_a_stale_sub_map_entry_on_a_trunking_system_fans_unit_data_out(clock) -> None:
    """Heard at a site an hour ago, never registered: the mesh gets it too, as for a hotspot entry."""
    s = _stack()
    s.config["_SUB_MAP"][bytes_3(MS)] = ("TRUNK", 1, time.time() - 3600, T1)
    s.send("HOTSPOTS", _unit_data(), HS_ADDR)
    assert len(s.obp_sent) == 1

    _clear(s)
    s.config["_SUB_MAP"][bytes_3(MS)] = ("TRUNK", 1, time.time() - 60, T1)  # fresh: served here only
    s.send("HOTSPOTS", _unit_data(0x0D0D0D0E), HS_ADDR)
    assert s.obp_sent == [] and len(s.dmrd_to("TRUNK", T1_ADDR)) == 1


def test_a_de_registered_radio_is_left_to_the_mesh(clock) -> None:
    s = _stack()
    _register(s, T1_ADDR, MS)
    clock.advance(30)
    s.send("TRUNK", dmrtc.create_deregistration_message(MS, int(time.time())), T1_ADDR)
    assert bytes_3(MS) not in s.config["_SUB_MAP"]
    _clear(s)
    clock.advance(30)

    s.send("HOTSPOTS", _unit_data(), HS_ADDR)
    assert len(s.obp_sent) == 1
    assert s.dmrd_to("TRUNK", T1_ADDR) == []


def test_de_registration_keeps_a_sub_map_entry_of_another_site(clock) -> None:
    s = _stack()
    _register(s, T1_ADDR, MS)
    clock.advance(30)
    s.config["_SUB_MAP"][bytes_3(MS)] = ("MASTER-C", 2, time.time(), HC)
    s.send("TRUNK", dmrtc.create_deregistration_message(MS, int(time.time())), T1_ADDR)

    assert s.config["_SUB_MAP"][bytes_3(MS)][0] == "MASTER-C"


def test_unit_data_to_a_radio_at_a_site_that_left_is_fanned_out_once_its_entry_is_stale(clock) -> None:
    s = _stack()
    _register(s, T1_ADDR, MS)
    s.send("TRUNK", DTCCL + T1, T1_ADDR)
    assert s.registry.location(MS) == ("TRUNK", T1)  # kept for its return
    _clear(s)
    clock.advance(3600)

    s.send("HOTSPOTS", _unit_data(), HS_ADDR)
    assert len(s.obp_sent) == 1
