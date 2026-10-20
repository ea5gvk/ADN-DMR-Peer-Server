# ADN DMR Peer Server - tests DMR Tier III uplink (sites -> network)
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

"""Calls from the Tier III sites: each stream on its own, wherever it must go.

A site (dmrtc behind DMRGateway ``TrunkingEnabled=1``) sends many calls at
once with the same slot bit, each 71 bytes with its call UUID. The whole
server path, wired as bootstrap: every stream keeps its own RX row (packet
control, START / END RX, LC, the one-conversation-per-TG rule), a frame routing
accepted goes to the other sites of the system that want it, the UUID travels
with the call, and hotspots only ever get the classic 55 bytes.
"""

from __future__ import annotations

import itertools
import time

import pytest

from adn_server.application.subscription.echo_seed import seed_echo_routing_table
from adn_server.application.subscription.store_sync import replace_store_from_routing_table
from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.trunking import DTCCL, DTCPING, TRUNK_DMRD_LEN
from tests.harness.deterministic import DeterministicScenario, FakeClock, PacketSpec
from tests.support import dmrtc_wire as dmrtc
from tests.support.trunk_site_stack import (
    T1,
    T1_ADDR,
    T2,
    T2_ADDR,
    T3,
    T3_ADDR,
    TrunkRoutingStack,
    by_stream,
    embedded_lc,
    header_lc,
    voice_frames,
)

pytestmark = pytest.mark.integration

HS, HS_ADDR = bytes_4(2140001), ("10.0.0.1", 62031)  # hotspot listening to TG 214 on TS2
HB, HB_ADDR = bytes_4(2140003), ("10.0.0.3", 62031)  # hotspot listening to TG 2141 on TS2
RADIO_A, RADIO_B = 2149050, 2149060  # radios at site T1
MS = 2149070  # a radio registered at another site
UUID_A, UUID_B = bytes(range(0xA0, 0xB0)), bytes(range(0xB0, 0xC0))
SID_A, SID_B = 0xA1A1A1A1, 0xB1B1B1B1
# Each call's own voice header LC: different service options / FID, so a mix-up shows.
LC_A = bytes((0x00, 0x00, 0x20)) + bytes_3(214) + bytes_3(RADIO_A)
LC_B = bytes((0x00, 0x10, 0x00)) + bytes_3(2141) + bytes_3(RADIO_B)


class _Reports:
    def __init__(self) -> None:
        self.events: list[str] = []

    def send_routing_event(self, event: str) -> None:
        self.events.append(event)

    def send_routing_table(self, *args, **kwargs) -> None:
        pass


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    """Frames 30 ms apart on the wall clock every layer reads."""
    c = FakeClock()
    monkeypatch.setattr(time, "time", c.time)
    return c


def _subscribe(s: TrunkRoutingStack, addr: tuple[str, int], *tgs: int, system: str = "TRUNK") -> None:
    s.send(system, dmrtc.create_group_subscription_message(list(tgs)), addr)
    s.transports[system].clear()


def _register(s: TrunkRoutingStack, addr: tuple[str, int], ms: int, *, system: str = "TRUNK") -> None:
    s.send(system, dmrtc.create_registration_message(ms, int(time.time())), addr)
    s.transports[system].clear()


def _stack(**kwargs) -> TrunkRoutingStack:
    s = TrunkRoutingStack(**kwargs)
    s.login_hotspot("HOTSPOTS", HS, HS_ADDR, options=b"TS2=214;")
    s.login_hotspot("HOTSPOTS", HB, HB_ADDR, options=b"TS2=2141;")
    for peer, addr in ((T1, T1_ADDR), (T2, T2_ADDR), (T3, T3_ADDR)):
        s.login("TRUNK", peer, addr)
    _subscribe(s, T1_ADDR, 214, 2141, 2142)
    _subscribe(s, T2_ADDR, 214, 2141, 2142)
    s.config["REPORTS"]["REPORT"] = True
    s.routing._reporting = s.reports = _Reports()
    return s


def _site_call(tg: int, radio: int, sid: int, uuid: bytes, *, site: bytes = T1, bursts: int = 4,
               lc: bytes | None = None) -> list[bytes]:
    """One over from a site as DMRGateway sends it: slot bit 1, 71 bytes (frame + call UUID)."""
    spec = PacketSpec(peer_id=int.from_bytes(site, "big"), rf_src=radio, dst_id=tg, slot=1, stream_id=sid)
    return [f + uuid for f in voice_frames(spec, bursts, lc=lc)]


def _site_private_call(radio: int, dst: int, sid: int, uuid: bytes, *, site: bytes = T1) -> list[bytes]:
    base = PacketSpec(peer_id=int.from_bytes(site, "big"), rf_src=radio, dst_id=dst, slot=1,
                      stream_id=sid, call_type="unit")
    frames = [DeterministicScenario.unit_voice_head_spec(base).data()]
    frames += [DeterministicScenario.unit_voice_burst_spec(base, seq=n, dtype_vseq=n).data() for n in range(1, 5)]
    frames += [DeterministicScenario.voice_term_spec(base, seq=5).data()]
    return [f + uuid for f in frames]


def _send(s: TrunkRoutingStack, clock: FakeClock, system: str, frames: list[bytes], addr: tuple[str, int]) -> list[float]:
    """Each frame 30 ms after the previous one; returns when each was sent."""
    sent = []
    for frame in frames:
        sent.append(clock.advance(0.03))
        s.send(system, frame, addr)
    return sent


def _events(s: TrunkRoutingStack, prefix: str) -> list[list[str]]:
    return [e.split(",") for e in s.reports.events if e.startswith(prefix)]


def _assert_site_copy(frames: list[bytes], sent: list[bytes], site: bytes, uuid: bytes) -> None:
    """The site's frames as sent, 71 bytes each, with the receiving site's ID and the caller's UUID."""
    assert len(frames) == len(sent)
    for got, orig in zip(frames, sent):
        assert len(got) == TRUNK_DMRD_LEN
        assert got[:11] == orig[:11] and got[11:15] == site and got[15:55] == orig[15:55]
        assert got[55:] == uuid


def test_two_concurrent_calls_from_one_site_on_one_slot_are_each_carried_whole(clock) -> None:
    """Regression: the slot STATUS took each interleaved frame for a new stream: packet control
    reset, the TG rule re-run mid-call (self-silencing), START spam, one shared LC, wrong durations."""
    s = _stack()
    a = _site_call(214, RADIO_A, SID_A, UUID_A, lc=LC_A)
    b = _site_call(2141, RADIO_B, SID_B, UUID_B, bursts=8, lc=LC_B)
    sent_at: dict[int, list[float]] = {SID_A: [], SID_B: []}
    sent_at[SID_A] += _send(s, clock, "TRUNK", a[:1], T1_ADDR)
    for fa, fb in itertools.zip_longest(a[1:], b):  # same slot bit, frame by frame
        if fa is not None:
            sent_at[SID_A] += _send(s, clock, "TRUNK", [fa], T1_ADDR)
        sent_at[SID_B] += _send(s, clock, "TRUNK", [fb], T1_ADDR)

    # The other site subscribed to both TGs gets both calls whole, with their UUIDs; not the caller's site.
    at_t2 = by_stream(s.dmrd_to("TRUNK", T2_ADDR))
    _assert_site_copy(at_t2[bytes_4(SID_A)], a, T2, UUID_A)
    _assert_site_copy(at_t2[bytes_4(SID_B)], b, T2, UUID_B)
    assert s.dmrd_to("TRUNK", T1_ADDR) == [] and s.dmrd_to("TRUNK", T3_ADDR) == []

    # Each hotspot gets its TG's call whole, 55 bytes, with that call's own LC.
    for addr, call, sid, lc in ((HS_ADDR, a, SID_A, LC_A), (HB_ADDR, b, SID_B, LC_B)):
        got = s.dmrd_to("HOTSPOTS", addr)
        assert [f[16:20] for f in got] == [bytes_4(sid)] * len(call)
        assert all(len(f) == 55 for f in got)
        assert header_lc(got[0]) == lc and embedded_lc(got) == lc

    # One START and one END per call, END with the call's own duration.
    assert sorted(int(e[4]) for e in _events(s, "GROUP VOICE,START,RX,TRUNK,")) == [SID_A, SID_B]
    assert sorted(int(e[4]) for e in _events(s, "GROUP VOICE,START,TX,HOTSPOTS,")) == [SID_A, SID_B]
    ends = {int(e[4]): float(e[9]) for e in _events(s, "GROUP VOICE,END,RX,TRUNK,")}
    assert len(_events(s, "GROUP VOICE,END,RX,TRUNK,")) == 2
    for sid, times in sent_at.items():
        assert ends[sid] == pytest.approx(times[-1] - times[0], abs=0.01)
    assert ends[SID_A] < ends[SID_B]


def test_duplicate_and_late_frames_are_still_dropped_per_stream(clock) -> None:
    s = _stack()
    a = _site_call(214, RADIO_A, SID_A, UUID_A)
    b = _site_call(2141, RADIO_B, SID_B, UUID_B)
    # a[1] twice (a duplicate after B's frame), b[1] again after b[2] (out of sequence).
    order = [a[0], b[0], a[1], b[1], a[1], b[2], b[1], a[2], b[3], a[3], b[4], a[4], b[5], a[5]]
    _send(s, clock, "TRUNK", order, T1_ADDR)

    at_t2 = by_stream(s.dmrd_to("TRUNK", T2_ADDR))
    _assert_site_copy(at_t2[bytes_4(SID_A)], a, T2, UUID_A)
    _assert_site_copy(at_t2[bytes_4(SID_B)], b, T2, UUID_B)
    assert len(s.dmrd_to("HOTSPOTS", HS_ADDR)) == len(a) and len(s.dmrd_to("HOTSPOTS", HB_ADDR)) == len(b)


def test_a_sites_call_reaches_the_subscribed_sites_of_its_system_but_never_itself(clock) -> None:
    s = _stack()
    _subscribe(s, T3_ADDR, 91)
    call = _site_call(2142, RADIO_A, SID_A, UUID_A)
    _send(s, clock, "TRUNK", call, T1_ADDR)

    _assert_site_copy(s.dmrd_to("TRUNK", T2_ADDR), call, T2, UUID_A)
    assert s.dmrd_to("TRUNK", T1_ADDR) == []
    assert s.dmrd_to("TRUNK", T3_ADDR) == []  # subscribed to another TG only


def test_site_frames_are_not_repeated_on_a_system_that_does_not_repeat(clock) -> None:
    s = _stack(system={"REPEAT": False})
    _send(s, clock, "TRUNK", _site_call(2142, RADIO_A, SID_A, UUID_A), T1_ADDR)

    assert s.dmrd_to("TRUNK", T2_ADDR) == []


def test_the_parrot_answers_only_the_site_that_keyed_the_echo_tg(clock) -> None:
    s = TrunkRoutingStack(classic=("HOTSPOTS", "ECHO"))
    replace_store_from_routing_table(s.store, seed_echo_routing_table(s.config))
    parrot, parrot_addr = bytes_4(9990), ("127.0.0.1", 54100)
    s.login_hotspot("ECHO", parrot, parrot_addr)
    for peer, addr in ((T1, T1_ADDR), (T2, T2_ADDR), (T3, T3_ADDR)):
        s.login("TRUNK", peer, addr)
    _send(s, clock, "TRUNK", _site_call(9990, 2149080, 0xC1C1C1C1, UUID_B, site=T2), T2_ADDR)  # an earlier over
    clock.advance(10.0)
    _send(s, clock, "TRUNK", _site_call(9990, RADIO_A, SID_A, UUID_A), T1_ADDR)
    assert all(s.dmrd_to("TRUNK", a) == [] for a in (T1_ADDR, T2_ADDR, T3_ADDR))  # no site hears an over

    clock.advance(2.0)  # the parrot replays the over, with the caller's ID
    replay = voice_frames(PacketSpec(peer_id=9990, rf_src=RADIO_A, dst_id=9990, slot=2, stream_id=0xE1E1E1E1))
    _send(s, clock, "ECHO", replay, parrot_addr)

    got = s.dmrd_to("TRUNK", T1_ADDR)
    assert len(got) == len(replay)
    assert all(len(f) == TRUNK_DMRD_LEN and f[11:15] == T1 and f[16:20] == bytes_4(0xE1E1E1E1) for f in got)
    assert s.dmrd_to("TRUNK", T2_ADDR) == [] and s.dmrd_to("TRUNK", T3_ADDR) == []


def test_private_call_from_a_site_reaches_the_site_of_the_registered_radio_on_its_system(clock) -> None:
    s = _stack()
    _register(s, T2_ADDR, MS)
    call = _site_private_call(RADIO_A, MS, SID_A, UUID_A)
    _send(s, clock, "TRUNK", call, T1_ADDR)

    _assert_site_copy(s.dmrd_to("TRUNK", T2_ADDR), call, T2, UUID_A)
    assert s.dmrd_to("TRUNK", T1_ADDR) == [] and s.dmrd_to("TRUNK", T3_ADDR) == []
    assert s.dmrd_to("HOTSPOTS", HS_ADDR) == [] and s.dmrd_to("HOTSPOTS", HB_ADDR) == []
    # One call on the monitor: received from T1, delivered to T2.
    assert len(_events(s, "PRIVATE VOICE,START,RX,TRUNK,")) == 1
    assert [e[-1] for e in _events(s, "PRIVATE VOICE,START,TX,TRUNK,")] == [str(int.from_bytes(T2, "big"))]
    assert len(_events(s, "PRIVATE VOICE,END,RX,TRUNK,")) == 1
    assert len(_events(s, "PRIVATE VOICE,END,TX,TRUNK,")) == 1


def test_private_call_from_a_site_reaches_a_site_of_another_trunking_system(clock) -> None:
    s = _stack(trunking=("TRUNK", "TRUNK-B"))
    tb, tb_addr = bytes_4(2149011), ("10.0.2.1", 40011)
    s.login("TRUNK-B", tb, tb_addr)
    _register(s, tb_addr, MS, system="TRUNK-B")
    call = _site_private_call(RADIO_A, MS, SID_A, UUID_A)
    _send(s, clock, "TRUNK", call, T1_ADDR)

    _assert_site_copy(s.dmrd_to("TRUNK-B", tb_addr), call, tb, UUID_A)
    assert all(s.dmrd_to("TRUNK", a) == [] for a in (T1_ADDR, T2_ADDR, T3_ADDR))


def test_concurrent_private_calls_from_one_site_to_hotspots_keep_their_targets_and_55_bytes(clock) -> None:
    """A site's call to a radio heard on a hotspot: per frame target, the hotspot gets 55 bytes."""
    s = _stack()
    s.config["_SUB_MAP"][bytes_3(2140101)] = ("HOTSPOTS", 2, time.time(), HS)
    s.config["_SUB_MAP"][bytes_3(2140301)] = ("HOTSPOTS", 2, time.time(), HB)
    a = _site_private_call(RADIO_A, 2140101, SID_A, UUID_A)
    b = _site_private_call(RADIO_B, 2140301, SID_B, UUID_B)
    for fa, fb in zip(a, b):
        _send(s, clock, "TRUNK", [fa, fb], T1_ADDR)

    for addr, call, sid in ((HS_ADDR, a, SID_A), (HB_ADDR, b, SID_B)):
        got = s.dmrd_to("HOTSPOTS", addr)
        assert [f[16:20] for f in got] == [bytes_4(sid)] * len(call)
        assert all(len(f) == 55 and f[15:55] == orig[15:55] for f, orig in zip(got, call))


def test_a_site_keying_a_tg_with_a_live_qso_is_silenced_at_its_other_sites_too(clock) -> None:
    s = _stack()
    qso = voice_frames(PacketSpec(peer_id=2140001, rf_src=2140101, dst_id=214, slot=2, stream_id=0x0A0A0A0A))
    site = _site_call(214, RADIO_A, SID_A, UUID_A)
    _send(s, clock, "HOTSPOTS", qso[:2], HS_ADDR)
    for fq, fs in zip(qso[2:], site):
        _send(s, clock, "HOTSPOTS", [fq], HS_ADDR)
        _send(s, clock, "TRUNK", [fs], T1_ADDR)

    assert {f[16:20] for f in s.dmrd_to("TRUNK", T2_ADDR)} == {bytes_4(0x0A0A0A0A)}  # the QSO, not the site
    assert bytes_4(SID_A) not in {f[16:20] for f in s.dmrd_to("HOTSPOTS", HS_ADDR)}
    assert [int(e[4]) for e in _events(s, "GROUP VOICE,START,RX,")] == [0x0A0A0A0A]


def test_a_hotspot_keying_a_tg_a_site_is_talking_on_is_silenced(clock) -> None:
    """The one-conversation-per-TG rule sees a site's call by its own row (no other system carries it)."""
    s = _stack(obp=())
    site = _site_call(2142, RADIO_A, SID_A, UUID_A, bursts=6)
    late = voice_frames(PacketSpec(peer_id=2140003, rf_src=2140301, dst_id=2142, slot=2, stream_id=0x0B0B0B0B))
    # Not right after burst C: its vseq (2) reads as a terminator in RX_TYPE, for a slot as for a row.
    _send(s, clock, "TRUNK", site[:3], T1_ADDR)
    for fs, fl in zip(site[3:], late):
        _send(s, clock, "TRUNK", [fs], T1_ADDR)
        _send(s, clock, "HOTSPOTS", [fl], HB_ADDR)

    assert {f[16:20] for f in s.dmrd_to("TRUNK", T2_ADDR)} == {bytes_4(SID_A)}
    assert s.dmrd_to("TRUNK", T1_ADDR) == []


def test_a_sites_stream_coming_back_on_another_system_is_a_loop(clock) -> None:
    """Stream-ID loop control sees a site's stream by its own row on the trunking MASTER."""
    s = _stack(obp=())
    site = _site_call(2142, RADIO_A, SID_A, UUID_A)
    _send(s, clock, "TRUNK", site[:3], T1_ADDR)
    echo = PacketSpec(peer_id=2140003, rf_src=2140301, dst_id=2142, slot=2, stream_id=SID_A)
    _send(s, clock, "HOTSPOTS", voice_frames(echo)[:3], HB_ADDR)

    assert len(s.dmrd_to("TRUNK", T2_ADDR)) == 3  # the site's three frames only
    assert s.dmrd_to("TRUNK", T1_ADDR) == []


def test_a_sites_caller_echoed_back_over_the_mesh_is_dropped_by_the_loop_guard(clock) -> None:
    """Same caller, same TG, new stream from another ingress while the site's over is on air."""
    s = _stack()
    site = _site_call(2142, RADIO_A, SID_A, UUID_A)
    _send(s, clock, "TRUNK", site[:3], T1_ADDR)
    echo = PacketSpec(peer_id=7301, rf_src=RADIO_A, dst_id=2142, slot=1, stream_id=0x0E0E0E0E)
    for frame in voice_frames(echo)[:3]:
        clock.advance(0.03)
        s.inject_obp("OBP", frame)

    assert {f[16:20] for f in s.dmrd_to("TRUNK", T2_ADDR)} == {bytes_4(SID_A)}
    assert s.dmrd_to("TRUNK", T1_ADDR) == []


def test_stream_rows_go_with_their_site_and_after_a_minute_idle(clock) -> None:
    s = _stack()
    p = s.protocols["TRUNK"]
    _send(s, clock, "TRUNK", _site_call(214, RADIO_A, SID_A, UUID_A)[:2], T1_ADDR)
    _send(s, clock, "TRUNK", _site_call(2141, 2149090, SID_B, UUID_B, site=T2)[:2], T2_ADDR)
    assert set(p.TRUNK_RX) == {bytes_4(SID_A), bytes_4(SID_B)}

    s.send("TRUNK", DTCCL + T2, T2_ADDR)
    assert set(p.TRUNK_RX) == {bytes_4(SID_A)}

    clock.advance(61.0)
    s.send("TRUNK", DTCPING + T1, T1_ADDR)  # T1 itself is still there
    p._master_maintenance_loop()
    assert p._peers[T1]["CONNECTION"] == "YES"
    assert p.TRUNK_RX == {}
