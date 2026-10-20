# ADN DMR Peer Server - tests DMR Tier III TRUNK legs in the subscription store
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

"""A trunking MASTER holds a TG in the store while one of its sites subscribes it.

The legs follow the registry (first holder arms them, last one releases them,
the table stays) and nothing meant for hotspots touches them: rule timers,
in-band SINGLE / TG 4000, the TG 4000 releases, OPTIONS, startup subscriptions
(SIGHUP). A BRIDGERESET turns them into plain legs; the resync re-arms what the
sites still hold.
"""

from __future__ import annotations

import copy
import time

import pytest

from adn_server.application.subscription.in_band_signalling_ops import apply_in_band_signalling_store
from adn_server.application.subscription.routing_table_export import export_routing_table
from adn_server.application.subscription.rule_timer_ops import apply_rule_timer_store
from adn_server.application.subscription.subscription_table_ops import ensure_dynamic_relay_store
from adn_server.application.subscription.trunk_legs_ops import release_trunk_legs_store, upsert_trunk_legs_store
from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.subscription import (
    ActivationPolicy,
    AudioChannel,
    SubscriptionId,
    SubscriptionPhase,
    SystemId,
    TgId,
)
from adn_server.infrastructure.hbp_constants import DTCCL
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore
from tests.harness.deterministic import PacketSpec
from tests.support import dmrtc_wire as dmrtc
from tests.support.trunk_site_stack import T1, T1_ADDR, T2, T2_ADDR, TrunkRoutingStack, voice_frames

HS, HS_ADDR = bytes_4(2140001), ("10.0.0.1", 62031)


def _leg(store, system: str, tg: int, slot: int):
    return store.get(SubscriptionId(channel=AudioChannel(tgid=TgId(tg), slot=slot), system=SystemId(system)))


def _trunk_legs(store, system: str, tg: int) -> list[bool]:
    """Per slot: is the system's leg an ACTIVE TRUNK leg."""
    out = []
    for slot in (1, 2):
        leg = _leg(store, system, tg, slot)
        out.append(leg is not None and leg.policy == ActivationPolicy.TRUNK and leg.is_active())
    return out


def _assert_plain_idle(store, system: str, tg: int) -> None:
    for slot in (1, 2):
        leg = _leg(store, system, tg, slot)
        assert leg is not None, "the system keeps its leg on the table"
        assert leg.policy == ActivationPolicy.INBAND and leg.state.phase == SubscriptionPhase.IDLE
        assert leg.triggers.on == (bytes_3(tg),)


def _stack() -> TrunkRoutingStack:
    s = TrunkRoutingStack()
    s.login("TRUNK", T1, T1_ADDR)
    s.login("TRUNK", T2, T2_ADDR)
    return s


def _subscribe(s: TrunkRoutingStack, addr, *tgs: int) -> None:
    s.send("TRUNK", dmrtc.create_group_subscription_message(list(tgs)), addr)


def _unsubscribe(s: TrunkRoutingStack, addr, *tgs: int) -> None:
    s.send("TRUNK", dmrtc.create_group_unsubscription_message(list(tgs)), addr)


# --- store ops -------------------------------------------------------------


@pytest.mark.unit
def test_trunk_legs_are_active_on_both_slots_and_read_as_static() -> None:
    store = InMemorySubscriptionStore()
    systems = {"TRUNK": {"MODE": "MASTER"}, "HOTSPOTS": {"MODE": "MASTER"}}
    ensure_dynamic_relay_store(store, 214, "HOTSPOTS", 2, 10.0, systems, 1000.0)
    upsert_trunk_legs_store(store, 214, "TRUNK")

    assert _trunk_legs(store, "TRUNK", 214) == [True, True]
    assert store.relay_tables_with_active_source("TRUNK", 1, 214) == ("214",)
    assert store.relay_tables_with_active_source("TRUNK", 2, 214) == ("214",)
    rows = [r for r in export_routing_table(store, now=1000.0)["214"] if r["SYSTEM"] == "TRUNK"]
    assert [(r["TS"], r["ACTIVE"], r["TO_TYPE"]) for r in rows] == [(1, True, "OFF"), (2, True, "OFF")]


@pytest.mark.unit
def test_release_turns_only_trunk_legs_back_into_idle_inband_legs() -> None:
    store = InMemorySubscriptionStore()
    systems = {"TRUNK": {"MODE": "MASTER"}, "HOTSPOTS": {"MODE": "MASTER"}}
    ensure_dynamic_relay_store(store, 214, "HOTSPOTS", 2, 10.0, systems, 1000.0)
    upsert_trunk_legs_store(store, 214, "TRUNK")

    assert release_trunk_legs_store(store, 214, "TRUNK", 600.0, 1000.0) is True
    _assert_plain_idle(store, "TRUNK", 214)
    assert _leg(store, "TRUNK", 214, 1).state.timer_expires_at == 1600.0
    assert _leg(store, "HOTSPOTS", 214, 2).is_active()  # the other systems' legs are left alone
    assert release_trunk_legs_store(store, 214, "TRUNK", 600.0, 1000.0) is False  # nothing left to release


@pytest.mark.unit
def test_trunk_legs_survive_rule_timers_in_both_modes() -> None:
    store = InMemorySubscriptionStore()
    systems = {"TRUNK": {"MODE": "MASTER", "SINGLE_MODE": False}, "HOTSPOTS": {"MODE": "MASTER"}}
    ensure_dynamic_relay_store(store, 214, "TRUNK", 1, 10.0, systems, 1000.0)
    upsert_trunk_legs_store(store, 214, "TRUNK")
    for single in (False, True):
        systems["TRUNK"]["SINGLE_MODE"] = single
        apply_rule_timer_store(store, systems, 1000.0 + 10 ** 7)
        assert store.has_table("214")
        assert _trunk_legs(store, "TRUNK", 214) == [True, True]


@pytest.mark.unit
@pytest.mark.parametrize(("single", "dst"), [(True, 91), (True, 4000), (False, 4000)])
def test_in_band_signalling_never_touches_trunk_legs(single: bool, dst: int) -> None:
    store = InMemorySubscriptionStore()
    systems = {"TRUNK": {"MODE": "MASTER", "SINGLE_MODE": single}, "HOTSPOTS": {"MODE": "MASTER"}}
    ensure_dynamic_relay_store(store, 214, "HOTSPOTS", 2, 10.0, systems, 1000.0)
    upsert_trunk_legs_store(store, 214, "TRUNK")
    for slot in (1, 2):
        apply_in_band_signalling_store(store, "TRUNK", slot, bytes_3(dst), 1001.0, systems)

    assert _trunk_legs(store, "TRUNK", 214) == [True, True]


# --- through DMRT and routing ---------------------------------------------


@pytest.mark.integration
def test_first_subscription_creates_the_table_with_trunk_legs() -> None:
    s = _stack()
    _subscribe(s, T1_ADDR, 214)

    assert s.store.has_table("214")
    assert _trunk_legs(s.store, "TRUNK", 214) == [True, True]
    hotspot_legs = [_leg(s.store, "HOTSPOTS", 214, slot) for slot in (1, 2)]
    assert all(leg is not None and not leg.is_active() for leg in hotspot_legs)
    assert _leg(s.store, "OBP", 214, 1).is_active()  # reaches the mesh like any user-activated TG


@pytest.mark.integration
def test_subscription_on_an_existing_table_turns_the_legs_into_trunk_legs() -> None:
    s = _stack()
    s.login_hotspot("HOTSPOTS", HS, HS_ADDR)
    for frame in voice_frames(PacketSpec(peer_id=2140001, rf_src=2140101, dst_id=214, slot=2, stream_id=0x0A0A0A0A)):
        s.send("HOTSPOTS", frame, HS_ADDR)
    assert s.store.has_table("214") and _trunk_legs(s.store, "TRUNK", 214) == [False, False]

    _subscribe(s, T1_ADDR, 214)
    assert _trunk_legs(s.store, "TRUNK", 214) == [True, True]
    assert _leg(s.store, "HOTSPOTS", 214, 2).is_active()


@pytest.mark.integration
def test_legs_stay_until_the_last_site_unsubscribes_then_the_table_is_kept() -> None:
    s = _stack()
    _subscribe(s, T1_ADDR, 214)
    _subscribe(s, T2_ADDR, 214)
    _unsubscribe(s, T1_ADDR, 214)
    assert _trunk_legs(s.store, "TRUNK", 214) == [True, True]

    _unsubscribe(s, T2_ADDR, 214)
    _assert_plain_idle(s.store, "TRUNK", 214)
    assert s.store.has_table("214")


@pytest.mark.integration
@pytest.mark.parametrize("leave", ["dtccl", "timeout", "relogin"])
def test_a_site_leaving_releases_the_tgs_only_it_held(leave: str) -> None:
    s = _stack()
    _subscribe(s, T1_ADDR, 214, 91)
    _subscribe(s, T2_ADDR, 91)
    if leave == "dtccl":
        s.send("TRUNK", DTCCL + T1, T1_ADDR)
    elif leave == "timeout":
        s.protocols["TRUNK"]._peers[T1]["LAST_PING"] = time.time() - 3600
        s.protocols["TRUNK"]._master_maintenance_loop()
    else:
        s.login("TRUNK", T1, T1_ADDR)

    _assert_plain_idle(s.store, "TRUNK", 214)
    assert _trunk_legs(s.store, "TRUNK", 91) == [True, True]  # T2 still holds it


@pytest.mark.integration
def test_trunk_legs_survive_the_tg4000_releases_and_options() -> None:
    s = _stack()
    _subscribe(s, T1_ADDR, 214)
    s.routing.deactivate_all_dynamic_relays("TRUNK")
    for slot in (1, 2):
        s.routing.release_dynamic_leg("TRUNK", 214, slot)
        s.routing.reset_static_tg(214, slot, 10.0, "TRUNK")
    s.routing.options_config_for_system("TRUNK", b"TS1=214;TS2=214;")
    s.routing.apply_static_tg_to_bridge(214)

    assert _trunk_legs(s.store, "TRUNK", 214) == [True, True]


@pytest.mark.integration
def test_trunk_legs_survive_a_reload_that_reapplies_the_startup_subscriptions() -> None:
    """SIGHUP: the system block is rebuilt from the YAML (same PEERS) and the startup
    subscriptions are applied again; the sites' TGs keep their legs and still route."""
    s = _stack()
    s.login_hotspot("HOTSPOTS", HS, HS_ADDR)
    _subscribe(s, T1_ADDR, 214)
    for name in ("TRUNK", "HOTSPOTS"):
        old = s.config["SYSTEMS"][name]
        fresh = copy.deepcopy({k: v for k, v in old.items() if k != "PEERS"})
        fresh["PEERS"] = old["PEERS"]
        s.config["SYSTEMS"][name] = fresh
        s.protocols[name].apply_system_config(s.config)
    s.routing.apply_startup_subscriptions()
    assert _trunk_legs(s.store, "TRUNK", 214) == [True, True]

    s.transports["TRUNK"].clear()
    call = voice_frames(PacketSpec(peer_id=2140001, rf_src=2140101, dst_id=214, slot=2, stream_id=0x0A0A0A0A))
    for frame in call:
        s.send("HOTSPOTS", frame, HS_ADDR)
    assert len(s.dmrd_to("TRUNK", T1_ADDR)) == len(call)


@pytest.mark.integration
def test_bridgereset_rearms_what_the_sites_still_hold() -> None:
    """A site re-logs and re-subscribes before the reset loop of the empty system ran."""
    s = TrunkRoutingStack()
    s.login("TRUNK", T1, T1_ADDR)
    s.send("TRUNK", DTCCL + T1, T1_ADDR)               # last site gone: _reset
    assert s.config["SYSTEMS"]["TRUNK"]["_reset"] is True
    s.login("TRUNK", T1, T1_ADDR)
    _subscribe(s, T1_ADDR, 214)
    s.routing.subscription_reset_loop()                # BRIDGERESET of TRUNK

    assert _trunk_legs(s.store, "TRUNK", 214) == [True, True]
    assert s.config["SYSTEMS"]["TRUNK"]["_reset"] is False


@pytest.mark.integration
def test_bridgereset_without_sites_leaves_plain_legs() -> None:
    s = TrunkRoutingStack()
    s.login("TRUNK", T1, T1_ADDR)
    _subscribe(s, T1_ADDR, 214)
    s.send("TRUNK", DTCCL + T1, T1_ADDR)
    s.routing.subscription_reset_loop()

    _assert_plain_idle(s.store, "TRUNK", 214)


@pytest.mark.integration
def test_removed_system_releases_its_legs() -> None:
    """Reload removed or flipped the system: its TGs are released even though it is gone."""
    s = _stack()
    _subscribe(s, T1_ADDR, 214)
    del s.config["SYSTEMS"]["TRUNK"]
    s.uc.system_removed("TRUNK")

    _assert_plain_idle(s.store, "TRUNK", 214)
    assert s.uc.registry.system_tgs("TRUNK") == []
