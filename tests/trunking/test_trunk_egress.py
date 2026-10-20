# ADN DMR Peer Server - tests DMR Tier III DMRD egress of the trunking MASTER
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

"""Every DMRD a trunking MASTER writes: to which sites, and in which form.

``send_system`` / ``send_peers`` choose the sites (subscription, echo source,
registration); ``send_peer`` writes one site's frame: its peer ID, the 55-byte
body and the stream's call UUID — 71 bytes, as DMRGateway (55 or 71 only) and
dmrtc (call identity = UUID) need. Hotspot gates never apply.
"""

from __future__ import annotations

import time

import pytest

from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.trunking import TRUNK_DMRD_LEN, derive_stream_uuid
from adn_server.infrastructure.hbp_constants import DMRA
from tests.harness.deterministic import DeterministicScenario, FakeClock, PacketSpec
from tests.support import dmrtc_wire as dmrtc
from tests.support.trunk_site_stack import T1, T1_ADDR, T2, T2_ADDR, T3, T3_ADDR, TrunkStack, voice_frames

pytestmark = pytest.mark.integration

UUID_A = bytes(range(0xA0, 0xB0))
STREAM = bytes_4(0x0A0B0C0D)
MS = 2149050


def _sites(**kwargs) -> TrunkStack:
    s = TrunkStack(**kwargs)
    for peer, addr in ((T1, T1_ADDR), (T2, T2_ADDR), (T3, T3_ADDR)):
        s.login("TRUNK", peer, addr)
    s.send("TRUNK", dmrtc.create_group_subscription_message([214]), T1_ADDR)
    s.send("TRUNK", dmrtc.create_group_subscription_message([214, 91]), T2_ADDR)
    s.transports["TRUNK"].clear()
    return s


def _group(tg: int = 214, **kwargs) -> bytes:
    return PacketSpec(peer_id=9990, rf_src=2140101, dst_id=tg, stream_id=0x0A0B0C0D, **kwargs).data()


def _unit(dst: int = MS) -> bytes:
    return PacketSpec(peer_id=9990, rf_src=2140101, dst_id=dst, stream_id=0x0A0B0C0D, call_type="unit").data()


def _dmrd(s: TrunkStack, addr) -> list[bytes]:
    return [p for p in s.to("TRUNK", addr) if p[:4] == b"DMRD"]


def test_group_frame_goes_to_each_subscribed_site_as_71_bytes() -> None:
    s = _sites()
    packet = _group()
    s.protocols["TRUNK"].send_system(packet)

    for peer, addr in ((T1, T1_ADDR), (T2, T2_ADDR)):
        (frame,) = _dmrd(s, addr)
        assert len(frame) == TRUNK_DMRD_LEN
        assert frame[:11] == packet[:11] and frame[11:15] == peer and frame[15:55] == packet[15:55]
        assert frame[55:] == derive_stream_uuid(STREAM)
    assert _dmrd(s, T3_ADDR) == []  # not subscribed


def test_short_frame_is_padded_with_ber_and_rssi() -> None:
    s = _sites()
    s.protocols["TRUNK"].send_system(_group()[:53], _ber=b"\x07", _rssi=b"\x2a")

    (frame,) = _dmrd(s, T1_ADDR)
    assert len(frame) == TRUNK_DMRD_LEN and frame[53:55] == b"\x07\x2a"


def test_the_originating_sites_uuid_is_kept() -> None:
    s = _sites()
    s.registry.note_stream_uuid(STREAM, UUID_A, time.time())
    s.protocols["TRUNK"].send_system(_group())

    assert [f[55:] for f in _dmrd(s, T1_ADDR)] == [UUID_A]


def test_echo_tg_goes_back_only_to_the_site_that_keyed_it() -> None:
    s = _sites()
    s.protocols["TRUNK"].send_system(_group(9990))
    assert all(_dmrd(s, a) == [] for a in (T1_ADDR, T2_ADDR, T3_ADDR))  # nobody keyed it

    s.registry.note_echo_source("TRUNK", 9990, T3, time.time())
    s.protocols["TRUNK"].send_system(_group(9990))
    assert len(_dmrd(s, T3_ADDR)) == 1
    assert _dmrd(s, T1_ADDR) == [] and _dmrd(s, T2_ADDR) == []


def test_echo_playback_keeps_its_site_until_it_ends(monkeypatch) -> None:
    """A long over to the parrot plays back as long: the site is kept a minute after the last frame it got."""
    clock = FakeClock()
    monkeypatch.setattr(time, "time", clock.time)
    s = _sites()
    s.registry.note_echo_source("TRUNK", 9990, T3, time.time())  # the site's over to the parrot ended
    clock.advance(2.0)
    for _ in range(130):  # 65 s of playback
        s.protocols["TRUNK"].send_system(_group(9990))
        clock.advance(0.5)
    assert len(_dmrd(s, T3_ADDR)) == 130

    clock.advance(61.0)
    s.protocols["TRUNK"].send_system(_group(9990))
    assert len(_dmrd(s, T3_ADDR)) == 130  # a minute after the playback: nobody keyed it


def test_unit_frame_goes_only_to_the_site_of_the_registered_radio() -> None:
    s = _sites()
    s.send("TRUNK", dmrtc.create_registration_message(MS, int(time.time())), T2_ADDR)
    s.transports["TRUNK"].clear()
    s.protocols["TRUNK"].send_system(_unit())

    assert len(_dmrd(s, T2_ADDR)) == 1
    assert _dmrd(s, T1_ADDR) == [] and _dmrd(s, T3_ADDR) == []


def test_unit_frame_follows_sub_map_here_and_is_never_broadcast() -> None:
    s = _sites()
    s.config["_SUB_MAP"][bytes_3(MS)] = ("TRUNK", 1, time.time(), T3)
    s.protocols["TRUNK"].send_system(_unit())
    assert len(_dmrd(s, T3_ADDR)) == 1

    s.transports["TRUNK"].clear()
    s.protocols["TRUNK"].send_system(_unit(2149999))  # nowhere known
    s.config["_SUB_MAP"][bytes_3(MS)] = ("HOTSPOTS", 2, time.time(), bytes_4(2140001))
    s.protocols["TRUNK"].send_system(_unit())  # heard on another system
    assert all(_dmrd(s, a) == [] for a in (T1_ADDR, T2_ADDR, T3_ADDR))


def test_unit_frame_follows_a_later_sighting_over_the_registration() -> None:
    s = _sites()
    s.send("TRUNK", dmrtc.create_registration_message(MS, int(time.time())), T2_ADDR)
    s.transports["TRUNK"].clear()
    s.config["_SUB_MAP"][bytes_3(MS)] = ("TRUNK", 1, time.time() + 1, T3)  # keyed up at T3 since
    s.protocols["TRUNK"].send_system(_unit())
    assert len(_dmrd(s, T3_ADDR)) == 1 and _dmrd(s, T2_ADDR) == []

    s.transports["TRUNK"].clear()
    s.config["_SUB_MAP"][bytes_3(MS)] = ("HOTSPOTS", 2, time.time() + 2, bytes_4(2140001))  # then on a hotspot
    s.protocols["TRUNK"].send_system(_unit())
    assert all(_dmrd(s, a) == [] for a in (T1_ADDR, T2_ADDR, T3_ADDR))


def test_send_peer_writes_the_site_frame_without_hotspot_gates() -> None:
    s = _sites()
    p = s.protocols["TRUNK"]
    head = DeterministicScenario.unit_voice_head_spec(PacketSpec(dst_id=MS, call_type="unit", stream_id=0x0A0B0C0D))
    p.send_peer(T3, head.data()[:53])  # unit data is built without BER/RSSI

    (frame,) = _dmrd(s, T3_ADDR)
    assert len(frame) == TRUNK_DMRD_LEN and frame[11:15] == T3 and frame[53:55] == b"\x00\x00"
    assert p._peer_voice_slots == {}  # no hotspot slot tracking


def test_send_peer_skips_a_site_that_is_not_logged_in() -> None:
    s = _sites()
    s.protocols["TRUNK"].send_peer(bytes_4(2149999), _group())
    assert s.transports["TRUNK"].sent == []


def test_prompts_and_dmra_are_not_sent_to_sites() -> None:
    s = _sites()
    p = s.protocols["TRUNK"]
    assert p.send_prompt_to_peer(T1, _group(4000)) is False
    assert p.send_dmra_to_peers([DMRA + b"\x00" * 11], slot=2, tgid=214) == 0
    assert s.transports["TRUNK"].sent == []


def test_a_sites_frame_routing_accepts_goes_to_the_other_subscribed_sites_only() -> None:
    s = _sites(dmrd_received=lambda *a, **k: True)
    for frame in voice_frames(PacketSpec(peer_id=2149001, rf_src=2149050, dst_id=214, stream_id=0x0D0D0D0D)):
        s.send("TRUNK", frame + UUID_A, T1_ADDR)

    got = _dmrd(s, T2_ADDR)
    assert len(got) == 6 and all(len(f) == TRUNK_DMRD_LEN and f[11:15] == T2 and f[55:] == UUID_A for f in got)
    assert _dmrd(s, T1_ADDR) == [] and _dmrd(s, T3_ADDR) == []


def test_a_sites_frame_routing_drops_goes_nowhere() -> None:
    s = _sites(dmrd_received=lambda *a, **k: False)
    for frame in voice_frames(PacketSpec(peer_id=2149001, rf_src=2149050, dst_id=214, stream_id=0x0D0D0D0D)):
        s.send("TRUNK", frame + UUID_A, T1_ADDR)

    assert all(_dmrd(s, a) == [] for a in (T1_ADDR, T2_ADDR, T3_ADDR))
