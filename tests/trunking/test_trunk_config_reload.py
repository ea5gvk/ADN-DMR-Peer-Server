# ADN DMR Peer Server - tests DMR Tier III TRUNKING flip on hot reload
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

"""SIGHUP that turns TRUNKING on or off on a kept system: remove + add, never a protocol in between.

Hotspots logged in with RPT* cannot stay on a system that now speaks DTC* (and
the reverse): the old protocol says goodbye to its peers, the system's sites
are forgotten, a new protocol starts with no peers on the same port. A reload
that keeps the flag keeps the protocol and its peers, as before.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from twisted.internet import defer

from adn_server.infrastructure.config_loader import YamlConfigLoader, process_acls
from adn_server.infrastructure.config_reload import ReloadResult, reload_server_config
from adn_server.infrastructure.hbp_constants import DTCACK, DTCCL, DTCL, MSTCL, RPTACK, RPTL
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol
from tests.support.trunk_site_stack import T1, T1_ADDR, TrunkStack

_RUNTIME_KEYS = ("PEERS", "OPTIONS", "REG_ACL", "SUB_ACL", "TG1_ACL", "TG2_ACL")


class _Port:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stopListening(self) -> defer.Deferred:
        self.stop_calls += 1
        return defer.succeed(None)


def _yaml_systems(stack: TrunkStack, *, trunking: bool) -> dict[str, Any]:
    """The YAML behind ``stack``'s systems (no runtime keys), with TRUNKING set as given."""
    systems = {}
    for name, cfg in stack.config["SYSTEMS"].items():
        new = {k: v for k, v in cfg.items() if not k.startswith("_") and k not in _RUNTIME_KEYS}
        new["TRUNKING"] = trunking
        new["PORT"] = 62037
        systems[name] = new
    return systems


def _reload(stack: TrunkStack, *, trunking: bool) -> tuple[ReloadResult, dict[str, Any], list, list[str]]:
    """Reload ``stack`` with TRUNKING set to ``trunking``; returns result, new config, listens, removed hooks."""
    incoming = {
        "GLOBAL": {"PING_TIME": 10, "MAX_MISSED": 3, "USE_ACL": False},
        "SYSTEMS": _yaml_systems(stack, trunking=trunking),
    }

    class _Loader(YamlConfigLoader):
        def load(self, _path: str) -> dict[str, Any]:
            data = copy.deepcopy(incoming)
            process_acls(data)
            return data

    new_config = copy.deepcopy(stack.config)
    new_config["_SUB_MAP"] = stack.config["_SUB_MAP"]
    listens: list = []
    removed: list[str] = []
    results: list[ReloadResult] = []

    def _create(name: str, cfg: dict[str, Any]) -> HBPProtocol:
        proto = HBPProtocol(name, cfg, router=stack.router, trunking_uc=stack.uc)
        proto.transport = stack.transports[name]
        return proto

    def _removed(name: str, _proto: Any) -> None:
        removed.append(name)
        stack.uc.system_removed(name)

    reload_server_config(
        new_config,
        "adn-server.yaml",
        _Loader(),
        stack.protocols,
        {name: _Port() for name in stack.protocols},
        create_protocol=_create,
        listen_udp=lambda name, bind, proto: listens.append((name, bind.port, proto)) or _Port(),
        stop_listener=lambda port: port.stopListening() if port else None,
        on_system_removed=_removed,
    ).addCallback(results.append)
    from twisted.internet import reactor

    reactor.runUntilCurrent()
    assert len(results) == 1
    return results[0], new_config, listens, removed


@pytest.mark.integration
def test_trunking_turned_off_drops_the_sites_and_starts_a_classic_protocol() -> None:
    s = TrunkStack()
    s.login("TRUNK", T1, T1_ADDR)
    s.send("TRUNK", b"DMRT\x03\x01" + (91).to_bytes(3, "big"), T1_ADDR)
    assert s.registry.peer_tgs("TRUNK", T1) == [91]
    old = s.protocols["TRUNK"]
    s.transports["TRUNK"].clear()

    result, new_config, listens, removed = _reload(s, trunking=False)

    assert result.rebound == ["TRUNK"] and removed == ["TRUNK"]
    assert s.transports["TRUNK"].sent == [(DTCCL + T1, T1_ADDR)]       # goodbye in its own dialect
    assert s.registry.peer_tgs("TRUNK", T1) == [] and s.registry.system_tgs("TRUNK") == []
    new = s.protocols["TRUNK"]
    assert new is not old and not new._trunking
    assert new_config["SYSTEMS"]["TRUNK"]["PEERS"] == {} and new_config["SYSTEMS"]["TRUNK"]["_reset"] is True
    assert [(name, port, proto) for name, port, proto in listens] == [("TRUNK", 62037, new)]

    s.transports["TRUNK"].clear()
    new.datagramReceived(RPTL + T1, T1_ADDR)                             # a hotspot can log in now
    assert s.transports["TRUNK"].sent[-1][0][:6] == RPTACK


@pytest.mark.integration
def test_trunking_turned_on_drops_the_hotspots_and_starts_a_trunking_protocol() -> None:
    s = TrunkStack(trunking=(), classic=("TRUNK",))
    t = s.transports["TRUNK"]
    p = s.protocols["TRUNK"]
    p._peers[T1] = {"CONNECTION": "YES", "SOCKADDR": T1_ADDR, "CALLSIGN": b"EA5GVK  ", "RADIO_ID": "2149001"}
    s.config["SYSTEMS"]["TRUNK"]["OPTIONS"] = "TS2=91;"                    # learned from the hotspot's RPTO

    result, new_config, listens, _removed = _reload(s, trunking=True)

    assert result.rebound == ["TRUNK"]
    assert t.sent == [(MSTCL + T1, T1_ADDR)]
    new = s.protocols["TRUNK"]
    assert new is not p and new._trunking and new._peers == {}
    assert "OPTIONS" not in new_config["SYSTEMS"]["TRUNK"]               # built from the YAML, not merged
    assert len(listens) == 1
    t.clear()
    new.datagramReceived(DTCL + T1, T1_ADDR)
    assert t.sent[-1][0][:6] == DTCACK and new._peers[T1]["TRUNKING"] is True


@pytest.mark.integration
def test_reload_keeping_trunking_keeps_the_protocol_and_its_sites() -> None:
    s = TrunkStack()
    s.login("TRUNK", T1, T1_ADDR)
    s.send("TRUNK", b"DMRT\x03\x01" + (91).to_bytes(3, "big"), T1_ADDR)
    old = s.protocols["TRUNK"]
    s.transports["TRUNK"].clear()

    result, new_config, listens, removed = _reload(s, trunking=True)

    assert result.updated == ["TRUNK"] and result.rebound == [] and removed == [] and listens == []
    assert s.protocols["TRUNK"] is old and old._trunking
    assert T1 in new_config["SYSTEMS"]["TRUNK"]["PEERS"]
    assert s.registry.peer_tgs("TRUNK", T1) == [91]
    assert s.transports["TRUNK"].sent == []

