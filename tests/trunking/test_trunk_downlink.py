# ADN DMR Peer Server - tests DMR Tier III downlink (network -> sites)
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

"""Calls from the network reach the Tier III sites that want them, as dmrtc needs them.

The whole server path, wired as bootstrap: a hotspot logs in on the proxy
MASTER and keys up (UDP -> routing -> trunking MASTER -> site), or an
OpenBridge stream enters routing; sites log in with DTC* and subscribe /
register with DMRT built by the dmrtc mirror. Every frame a site gets must be
71 bytes (DMRGateway takes 55 or 71 only; dmrtc keys its call on the UUID), with
one UUID per stream and the stream's own LC — a site carries many calls at once.
"""

from __future__ import annotations

import time

import pytest
from bitarray import bitarray

from adn_server.domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, bytes_3, bytes_4
from adn_server.domain.dmr import bptc, decode
from adn_server.domain.trunking import TRUNK_DMRD_LEN
from adn_server.infrastructure.hbp_constants import DTCCL
from tests.harness.deterministic import DeterministicScenario, FakeClock, PacketSpec
from tests.support import dmrtc_wire as dmrtc
from tests.support.trunk_site_stack import T1, T1_ADDR, T2, T2_ADDR, TrunkRoutingStack, voice_frames

pytestmark = pytest.mark.integration

HS, HS_ADDR = bytes_4(2140001), ("10.0.0.1", 62031)
HC, HC_ADDR = bytes_4(2140002), ("10.0.0.2", 62031)
MS = 2149050           # a radio registered at site T1
HS_RADIO = 2140101     # the hotspot's radio


def _stack(**kwargs) -> TrunkRoutingStack:
    s = TrunkRoutingStack(**kwargs)
    s.login_hotspot("HOTSPOTS", HS, HS_ADDR)
    s.login("TRUNK", T1, T1_ADDR)
    s.login("TRUNK", T2, T2_ADDR)
    return s


def _subscribe(s: TrunkRoutingStack, addr: tuple[str, int], *tgs: int) -> None:
    s.send("TRUNK", dmrtc.create_group_subscription_message(list(tgs)), addr)
    s.transports["TRUNK"].clear()


def _hotspot_call(tg: int, stream_id: int, *, rf_src: int = HS_RADIO, peer: bytes = HS) -> list[bytes]:
    return voice_frames(PacketSpec(peer_id=int.from_bytes(peer, "big"), rf_src=rf_src, dst_id=tg, slot=2, stream_id=stream_id))


def _by_stream(frames: list[bytes]) -> dict[bytes, list[bytes]]:
    out: dict[bytes, list[bytes]] = {}
    for f in frames:
        out.setdefault(f[16:20], []).append(f)
    return out


def _header_lc(frame: bytes) -> bytes:
    assert frame[15] & 0x3F == (HBPF_DATA_SYNC << 4) | HBPF_SLT_VHEAD
    return decode.voice_head_term(frame[20:53])["LC"]


def _embedded_lc(frames: list[bytes]) -> bytes:
    by_vseq = {f[15] & 0x0F: f for f in frames if (f[15] & 0x30) >> 4 == 0}
    bits = bitarray(endian="big")
    for vseq in (1, 2, 3, 4):
        bits += decode.voice(by_vseq[vseq][20:53])["EMBED"]
    return bptc.decode_emblc(bits)


def _assert_site_frames(frames: list[bytes], site: bytes, *, tg: int, rf_src: int) -> bytes:
    """Every frame 71 bytes, to this site, with the call's TG / source; one non-zero UUID. Returns it."""
    assert frames
    assert all(len(f) == TRUNK_DMRD_LEN for f in frames)
    assert all(f[11:15] == site and f[8:11] == bytes_3(tg) and f[5:8] == bytes_3(rf_src) for f in frames)
    uuids = {f[55:71] for f in frames}
    assert len(uuids) == 1 and any(next(iter(uuids)))
    lc = _header_lc(frames[0])
    assert lc[3:6] == bytes_3(tg) and lc[6:9] == bytes_3(rf_src)
    emb = _embedded_lc(frames)
    assert emb[3:6] == bytes_3(tg) and emb[6:9] == bytes_3(rf_src)
    return next(iter(uuids))


def test_hotspot_group_call_reaches_the_subscribed_site_as_71_byte_frames() -> None:
    s = _stack()
    _subscribe(s, T1_ADDR, 214)
    _subscribe(s, T2_ADDR, 91)
    call = _hotspot_call(214, 0x0A0A0A0A)
    for frame in call:
        s.send("HOTSPOTS", frame, HS_ADDR)

    got = s.dmrd_to("TRUNK", T1_ADDR)
    assert len(got) == len(call)
    _assert_site_frames(got, T1, tg=214, rf_src=HS_RADIO)
    assert [f[16:20] for f in got] == [bytes_4(0x0A0A0A0A)] * len(call)
    assert s.dmrd_to("TRUNK", T2_ADDR) == []  # T2 did not subscribe 214


def test_openbridge_group_call_reaches_the_subscribed_site() -> None:
    s = _stack()
    _subscribe(s, T1_ADDR, 91)
    call = voice_frames(PacketSpec(peer_id=7301, rf_src=3120001, dst_id=91, slot=1, stream_id=0x0B0B0B0B))
    for frame in call:
        s.inject_obp("OBP", frame)

    got = s.dmrd_to("TRUNK", T1_ADDR)
    assert len(got) == len(call)
    _assert_site_frames(got, T1, tg=91, rf_src=3120001)
    assert s.dmrd_to("TRUNK", T2_ADDR) == []


def test_every_subscribed_site_gets_the_call_with_the_same_uuid() -> None:
    s = _stack()
    _subscribe(s, T1_ADDR, 214)
    _subscribe(s, T2_ADDR, 214)
    for frame in _hotspot_call(214, 0x0A0A0A0A):
        s.send("HOTSPOTS", frame, HS_ADDR)

    u1 = _assert_site_frames(s.dmrd_to("TRUNK", T1_ADDR), T1, tg=214, rf_src=HS_RADIO)
    u2 = _assert_site_frames(s.dmrd_to("TRUNK", T2_ADDR), T2, tg=214, rf_src=HS_RADIO)
    assert u1 == u2


def test_two_concurrent_calls_on_one_slot_both_reach_the_site_frame_by_frame() -> None:
    """Regression: a trunking MASTER with one site took the classic 2-slot contention:
    the second call on the slot was dropped, and both shared the slot row's LC."""
    s = _stack(classic=("HOTSPOTS", "MASTER-C"))
    s.login_hotspot("MASTER-C", HC, HC_ADDR)
    _subscribe(s, T1_ADDR, 214, 2141)
    a = _hotspot_call(214, 0x0A0A0A0A)
    c = _hotspot_call(2141, 0x0C0C0C0C, rf_src=2140202, peer=HC)
    for fa, fc in zip(a, c):  # interleaved, both on TS2 at the source
        s.send("HOTSPOTS", fa, HS_ADDR)
        s.send("MASTER-C", fc, HC_ADDR)

    got = s.dmrd_to("TRUNK", T1_ADDR)
    assert [f[16:20] for f in got] == [bytes_4(0x0A0A0A0A), bytes_4(0x0C0C0C0C)] * len(a)
    streams = _by_stream(got)
    ua = _assert_site_frames(streams[bytes_4(0x0A0A0A0A)], T1, tg=214, rf_src=HS_RADIO)
    uc = _assert_site_frames(streams[bytes_4(0x0C0C0C0C)], T1, tg=2141, rf_src=2140202)
    assert ua != uc
    assert len({f[15] & 0x80 for f in got}) == 1  # both on the same slot row of the trunking MASTER


def test_hotspot_and_openbridge_calls_together_reach_the_site() -> None:
    s = _stack()
    _subscribe(s, T1_ADDR, 214, 91)
    a = _hotspot_call(214, 0x0A0A0A0A)
    o = voice_frames(PacketSpec(peer_id=7301, rf_src=3120001, dst_id=91, slot=1, stream_id=0x0B0B0B0B))
    for fa, fo in zip(a, o):
        s.send("HOTSPOTS", fa, HS_ADDR)
        s.inject_obp("OBP", fo)

    streams = _by_stream(s.dmrd_to("TRUNK", T1_ADDR))
    assert len(streams[bytes_4(0x0A0A0A0A)]) == len(a) and len(streams[bytes_4(0x0B0B0B0B)]) == len(o)
    _assert_site_frames(streams[bytes_4(0x0A0A0A0A)], T1, tg=214, rf_src=HS_RADIO)
    _assert_site_frames(streams[bytes_4(0x0B0B0B0B)], T1, tg=91, rf_src=3120001)


def test_a_subscribed_site_does_not_cut_a_hotspot_call_on_the_shared_master(monkeypatch) -> None:
    """Regression guard: the trunking slot's TX leg of the call itself was taken for another QSO on
    its TG, so a hotspot sharing the MASTER slot with another one was silenced everywhere mid-call."""
    clock = FakeClock()
    monkeypatch.setattr(time, "time", clock.time)
    s = _stack()
    hb, hb_addr = bytes_4(2140003), ("10.0.0.3", 62031)
    s.login_hotspot("HOTSPOTS", hb, hb_addr)
    obp: list[bytes] = []
    s.protocols["OBP"].send_system = lambda packet, **kw: obp.append(packet)
    clock.advance(10)
    _subscribe(s, T1_ADDR, 214)
    a = voice_frames(PacketSpec(peer_id=2140001, rf_src=HS_RADIO, dst_id=214, slot=2, stream_id=0x0A0A0A01), bursts=10)
    b = voice_frames(PacketSpec(peer_id=2140003, rf_src=2140301, dst_id=3100, slot=2, stream_id=0x0B0B0B02), bursts=6)
    order = [(HS_ADDR, f) for f in a[:3]]
    for i, fb in enumerate(b):
        order += [(hb_addr, fb), (HS_ADDR, a[3 + i])]
    order += [(HS_ADDR, f) for f in a[3 + len(b):]]
    for addr, frame in order:
        clock.advance(0.03)
        s.send("HOTSPOTS", frame, addr)

    assert len(_by_stream(obp).get(bytes_4(0x0A0A0A01), [])) == len(a)
    assert len(_by_stream(s.dmrd_to("TRUNK", T1_ADDR)).get(bytes_4(0x0A0A0A01), [])) == len(a)


def test_a_call_to_the_sites_that_loses_its_terminator_after_burst_c_still_ends(monkeypatch) -> None:
    """Voice burst C carries the terminator's vseq (2): its stream's TX leg on the trunking slot stayed forever."""
    clock = FakeClock()
    monkeypatch.setattr(time, "time", clock.time)
    s = _stack()
    _subscribe(s, T1_ADDR, 214)
    events: list[str] = []
    s.routing._send_routing_event = lambda event: events.append(event) or True
    for frame in _hotspot_call(214, 0x0A0A0A0A)[:3]:  # header, bursts B and C: the terminator is lost
        clock.advance(0.03)
        s.send("HOTSPOTS", frame, HS_ADDR)
    trunk = s.protocols["TRUNK"]
    assert any(trunk.STATUS[slot].get("TX_STREAMS") for slot in (1, 2))

    clock.advance(30)
    s.routing.stream_trimmer_loop()
    assert not any(trunk.STATUS[slot].get("TX_STREAMS") for slot in (1, 2))
    assert [e for e in events if e.startswith(f"GROUP VOICE,END,TX,TRUNK,{0x0A0A0A0A},")]


def test_unsubscribed_or_gone_site_gets_nothing() -> None:
    s = _stack()
    _subscribe(s, T1_ADDR, 214)
    _subscribe(s, T2_ADDR, 214)
    s.send("TRUNK", dmrtc.create_group_unsubscription_message([214]), T2_ADDR)
    s.send("TRUNK", DTCCL + T1, T1_ADDR)
    s.transports["TRUNK"].clear()
    for frame in _hotspot_call(214, 0x0A0A0A0A):
        s.send("HOTSPOTS", frame, HS_ADDR)

    assert s.dmrd_to("TRUNK", T1_ADDR) == [] and s.dmrd_to("TRUNK", T2_ADDR) == []


def _register(s: TrunkRoutingStack, addr: tuple[str, int], ms: int) -> None:
    s.send("TRUNK", dmrtc.create_registration_message(ms, int(time.time())), addr)
    s.transports["TRUNK"].clear()


def test_private_call_goes_only_to_the_site_the_radio_is_registered_at() -> None:
    s = _stack()
    _register(s, T1_ADDR, MS)
    base = PacketSpec(peer_id=2140001, rf_src=HS_RADIO, dst_id=MS, slot=2, stream_id=0x0E0E0E0E, call_type="unit")
    frames = [DeterministicScenario.unit_voice_head_spec(base).data()]
    frames += [DeterministicScenario.unit_voice_burst_spec(base, seq=n, dtype_vseq=n).data() for n in range(1, 5)]
    for frame in frames:
        s.send("HOTSPOTS", frame, HS_ADDR)

    got = s.dmrd_to("TRUNK", T1_ADDR)
    assert len(got) == len(frames)
    assert all(len(f) == TRUNK_DMRD_LEN and f[11:15] == T1 and f[8:11] == bytes_3(MS) for f in got)
    assert all(f[15] & 0x40 for f in got)
    assert len({f[55:71] for f in got}) == 1
    assert s.dmrd_to("TRUNK", T2_ADDR) == []


def _private_call(src_peer: bytes, rf_src: int, dst: int, stream_id: int) -> list[bytes]:
    base = PacketSpec(peer_id=int.from_bytes(src_peer, "big"), rf_src=rf_src, dst_id=dst, slot=2,
                      stream_id=stream_id, call_type="unit")
    return [DeterministicScenario.unit_voice_head_spec(base).data()] + [
        DeterministicScenario.unit_voice_burst_spec(base, seq=n, dtype_vseq=n).data() for n in range(1, 5)
    ]


def test_concurrent_private_calls_to_two_sites_do_not_take_each_others_target() -> None:
    """Regression guard: the private call target was kept on the routing instance, so a
    second call starting elsewhere sent the rest of the first call to its own target."""
    s = _stack(classic=("HOTSPOTS", "MASTER-C"))
    s.login_hotspot("MASTER-C", HC, HC_ADDR)
    _register(s, T1_ADDR, MS)
    _register(s, T2_ADDR, 2149060)
    a = _private_call(HS, HS_RADIO, MS, 0x0E0E0E0E)
    c = _private_call(HC, 2140202, 2149060, 0x0F0F0F0F)
    for fa, fc in zip(a, c):
        s.send("HOTSPOTS", fa, HS_ADDR)
        s.send("MASTER-C", fc, HC_ADDR)

    assert [f[16:20] for f in s.dmrd_to("TRUNK", T1_ADDR)] == [bytes_4(0x0E0E0E0E)] * len(a)
    assert [f[16:20] for f in s.dmrd_to("TRUNK", T2_ADDR)] == [bytes_4(0x0F0F0F0F)] * len(c)


def test_registration_wins_over_an_older_sub_map_entry_elsewhere() -> None:
    s = _stack(classic=("HOTSPOTS", "MASTER-C"))
    s.login_hotspot("MASTER-C", HC, HC_ADDR)
    _register(s, T1_ADDR, MS)
    s.config["_SUB_MAP"][bytes_3(MS)] = ("MASTER-C", 2, time.time() - 60, HC)  # heard there before it registered
    s.transports["MASTER-C"].clear()
    for frame in _private_call(HS, HS_RADIO, MS, 0x0E0E0E0E):
        s.send("HOTSPOTS", frame, HS_ADDR)

    assert len(s.dmrd_to("TRUNK", T1_ADDR)) == 5
    assert s.transports["MASTER-C"].sent == []


def test_unit_data_goes_once_and_only_to_the_site_the_radio_is_registered_at() -> None:
    s = _stack()
    _register(s, T1_ADDR, MS)
    base = PacketSpec(peer_id=2140001, rf_src=HS_RADIO, dst_id=MS, slot=2, stream_id=0x0D0D0D0D, call_type="unit")
    frames = [DeterministicScenario.unit_data_header_spec(base, dtype_vseq=dt).data() for dt in (6, 7)]
    for frame in frames:
        s.send("HOTSPOTS", frame, HS_ADDR)

    got = s.dmrd_to("TRUNK", T1_ADDR)
    assert [f[15] & 0x0F for f in got] == [6, 7]  # once each: not again by the private call path
    assert all(len(f) == TRUNK_DMRD_LEN and f[11:15] == T1 for f in got)
    assert s.dmrd_to("TRUNK", T2_ADDR) == []


def test_radio_that_roamed_gets_its_calls_at_the_new_site() -> None:
    s = _stack()
    _register(s, T1_ADDR, MS)
    _register(s, T2_ADDR, MS)
    base = PacketSpec(peer_id=2140001, rf_src=HS_RADIO, dst_id=MS, slot=2, stream_id=0x0E0E0E0E, call_type="unit")
    s.send("HOTSPOTS", DeterministicScenario.unit_voice_head_spec(base).data(), HS_ADDR)

    assert s.dmrd_to("TRUNK", T1_ADDR) == []
    assert len(s.dmrd_to("TRUNK", T2_ADDR)) == 1


def test_unit_data_to_a_radio_at_no_site_is_never_broadcast_to_the_sites() -> None:
    s = _stack()
    s.config["_SUB_MAP"][bytes_3(MS)] = ("TRUNK", 1, time.time(), bytes_4(2149999))  # a site that is gone
    base = PacketSpec(peer_id=2140001, rf_src=HS_RADIO, dst_id=MS, slot=2, stream_id=0x0D0D0D0D, call_type="unit")
    s.send("HOTSPOTS", DeterministicScenario.unit_data_header_spec(base).data(), HS_ADDR)
    s.send("HOTSPOTS", DeterministicScenario.unit_voice_head_spec(base).data(), HS_ADDR)

    assert s.dmrd_to("TRUNK", T1_ADDR) == [] and s.dmrd_to("TRUNK", T2_ADDR) == []


def test_unit_data_to_a_site_id_is_not_matched_like_a_hotspot() -> None:
    """A site is not a hotspot: unit data to a 7-digit ID that is a site's repeater ID prefix
    (the hotspot 6/7-digit fallback) does not go to it."""
    s = _stack(system={"GROUP_HANGTIME": 0})  # no idle wait: a hotspot there would get it
    base = PacketSpec(peer_id=2140001, rf_src=HS_RADIO, dst_id=2149001, slot=2, stream_id=0x0D0D0D0D, call_type="unit")
    s.send("HOTSPOTS", DeterministicScenario.unit_data_header_spec(base).data(), HS_ADDR)

    assert s.dmrd_to("TRUNK", T1_ADDR) == []


def test_classic_master_still_contends_two_calls_on_one_slot() -> None:
    """Unchanged for hotspots: a single-hotspot MASTER keeps the 2-slot contention."""
    s = TrunkRoutingStack(trunking=(), classic=("HOTSPOTS", "MASTER-C", "MASTER-B"), obp=(), system={"GROUP_HANGTIME": 0})
    s.login_hotspot("HOTSPOTS", HS, HS_ADDR)
    s.login_hotspot("MASTER-C", HC, HC_ADDR)
    hb, hb_addr = bytes_4(2140003), ("10.0.0.3", 62031)
    s.login_hotspot("MASTER-B", hb, hb_addr, options=b"TS2=214,2141;")
    a = _hotspot_call(214, 0x0A0A0A0A)
    c = _hotspot_call(2141, 0x0C0C0C0C, rf_src=2140202, peer=HC)
    for fa, fc in zip(a, c):
        s.send("HOTSPOTS", fa, HS_ADDR)
        s.send("MASTER-C", fc, HC_ADDR)

    got = [f for f in s.to("MASTER-B", hb_addr) if f[:4] == b"DMRD"]
    assert all(len(f) == 55 for f in got)
    streams = _by_stream(got)
    assert len(streams.get(bytes_4(0x0A0A0A0A), [])) == len(a)
    assert len(streams.get(bytes_4(0x0C0C0C0C), [])) < len(c)
