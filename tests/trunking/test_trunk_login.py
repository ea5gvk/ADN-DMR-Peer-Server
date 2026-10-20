# ADN DMR Peer Server - tests DMR Tier III login on the HBP MASTER
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

"""DTC* login, keepalive and close on a ``TRUNKING: true`` MASTER, as DMRGateway drives it.

DMRGateway (TrunkingEnabled=1) tells dmrtc "MasterLogin" only after a DTCACK
and "MasterClose" only on a DTCCL, so a Tier III site must never be answered
with a classic RPT*/MST* name. Classic hotspots are refused on such a system,
and a classic MASTER keeps ignoring DTC* exactly as before.
"""

from __future__ import annotations

import hashlib
import logging
import time
from unittest.mock import MagicMock

import pytest

from adn_server.domain import bytes_4
from adn_server.infrastructure.hbp_constants import (
    DMRG,
    DTCACK,
    DTCCL,
    DTCG,
    DTCK,
    DTCL,
    DTCNAK,
    DTCO,
    DTCPING,
    DTCPONG,
    MSTCL,
    MSTNAK,
    RPTACK,
    RPTG,
    RPTK,
    RPTL,
    RPTPING,
)
from tests.support import dmrtc_wire as dmrtc
from tests.support.trunk_site_stack import PASS, T1, T1_ADDR, T2, T2_ADDR, TrunkStack, dtcc

pytestmark = pytest.mark.integration


def _no_classic_reply(sent: list[tuple[bytes, tuple[str, int]]]) -> bool:
    return all(data[:3] in (b"DTC", b"DMR") for data, _addr in sent)


def test_full_dtc_login_exchange_keepalive_and_close(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    s = TrunkStack()
    p, t = s.protocols["TRUNK"], s.transports["TRUNK"]

    s.send("TRUNK", DTCL + T1, T1_ADDR)
    assert len(t.sent) == 1
    challenge, addr = t.sent[0]
    assert addr == T1_ADDR and challenge[:6] == DTCACK and len(challenge) == 10
    assert challenge[6:10] == bytes_4(p._peers[T1]["SALT"])
    assert p._peers[T1]["TRUNKING"] is True and p._peers[T1]["CONNECTION"] == "CHALLENGE_SENT"

    s.send("TRUNK", DTCK + T1 + hashlib.sha256(challenge[6:10] + PASS).digest(), T1_ADDR)   # 40 bytes
    assert t.sent[-1] == (DTCACK + T1, T1_ADDR)
    assert p._peers[T1]["CONNECTION"] == "WAITING_CONFIG"

    config = dtcc(T1)
    assert len(config) == 302
    s.send("TRUNK", config, T1_ADDR)
    assert t.sent[-1] == (DTCACK + T1, T1_ADDR)                       # the ACK that makes "MasterLogin"
    peer = p._peers[T1]
    assert peer["CONNECTION"] == "YES"
    assert peer["CALLSIGN"].rstrip() == b"EA5GVK" and peer["LATITUDE"] == b"+40.4168"
    assert p.trunking_peer_connected(T1)

    s.send("TRUNK", DTCPING + T1, T1_ADDR)
    assert t.sent[-1] == (DTCPONG + T1, T1_ADDR) and len(t.sent[-1][0]) == 11
    assert peer["PINGS_RECEIVED"] == 1

    assert _no_classic_reply(t.sent)
    t.clear()
    s.send("TRUNK", DTCCL + T1, T1_ADDR)
    assert T1 not in p._peers
    assert t.sent == []                                                  # the site is gone: no answer
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_dtco_is_acknowledged_and_kept_on_the_site_only() -> None:
    s = TrunkStack()
    options_seen: list = []
    s.protocols["TRUNK"]._on_options_received = lambda *a: options_seen.append(a)
    sys_cfg = s.config["SYSTEMS"]["TRUNK"]
    s.login("TRUNK", T1, T1_ADDR)

    s.send("TRUNK", DTCO + T1 + b"TS2=91;PASS=secret", T1_ADDR)

    assert s.transports["TRUNK"].sent == [(DTCACK + T1, T1_ADDR)]       # the final ACK of a DTCO login
    peer = s.protocols["TRUNK"]._peers[T1]
    assert peer["TRUNK_OPTIONS"] == "TS2=91;PASS=secret"
    assert "OPTIONS" not in peer and "OPTIONS" not in sys_cfg
    assert options_seen == []


def test_login_with_options_as_dmrgateway_sends_it() -> None:
    s = TrunkStack()
    s.login("TRUNK", T1, T1_ADDR, options=b"Some options")
    assert s.protocols["TRUNK"]._peers[T1]["TRUNK_OPTIONS"] == "Some options"


def test_dtco_from_a_site_not_logged_in_gets_dtcnak() -> None:
    s = TrunkStack()
    s.send("TRUNK", DTCO + T1 + b"x", T1_ADDR)
    assert s.transports["TRUNK"].sent == [(DTCNAK + T1, T1_ADDR)]


def test_wrong_password_gets_dtcnak_and_the_site_is_dropped() -> None:
    s = TrunkStack()
    t = s.transports["TRUNK"]
    s.send("TRUNK", DTCL + T1, T1_ADDR)
    s.send("TRUNK", DTCK + T1 + hashlib.sha256(t.sent[-1][0][6:10] + b"wrong").digest(), T1_ADDR)
    assert t.sent[-1] == (DTCNAK + T1, T1_ADDR)
    assert T1 not in s.protocols["TRUNK"]._peers


def test_individual_password_takes_precedence() -> None:
    s = TrunkStack(get_user_password=lambda radio_id: b"own-pass" if radio_id == 2149001 else None)
    t = s.transports["TRUNK"]
    s.send("TRUNK", DTCL + T1, T1_ADDR)
    s.send("TRUNK", DTCK + T1 + hashlib.sha256(t.sent[-1][0][6:10] + b"own-pass").digest(), T1_ADDR)
    assert t.sent[-1] == (DTCACK + T1, T1_ADDR)


@pytest.mark.parametrize("packet", [DTCK + T1 + b"\x00" * 32, dtcc(T1), DTCPING + T1])
def test_dtc_steps_out_of_order_get_dtcnak(packet: bytes) -> None:
    s = TrunkStack()
    s.send("TRUNK", packet, T1_ADDR)
    assert s.transports["TRUNK"].sent == [(DTCNAK + T1, T1_ADDR)]
    assert T1 not in s.protocols["TRUNK"]._peers


def test_too_many_sites_get_dtcnak() -> None:
    s = TrunkStack(system={"MAX_PEERS": 1})
    s.login("TRUNK", T1, T1_ADDR)
    s.send("TRUNK", DTCL + T2, T2_ADDR)
    assert s.transports["TRUNK"].sent == [(DTCNAK + T2, T2_ADDR)]


def test_dtc_steps_from_a_peer_that_did_not_log_in_with_dtcl_are_refused() -> None:
    s = TrunkStack()
    p = s.protocols["TRUNK"]
    p._peers[T1] = {"CONNECTION": "YES", "SOCKADDR": T1_ADDR, "CALLSIGN": b"", "LAST_PING": time.time()}
    s.send("TRUNK", DTCPING + T1, T1_ADDR)
    assert s.transports["TRUNK"].sent == [(DTCNAK + T1, T1_ADDR)]
    assert "PINGS_RECEIVED" not in p._peers[T1]


def test_classic_login_is_refused_on_a_trunking_system(caplog) -> None:
    s = TrunkStack()
    caplog.set_level(logging.WARNING)
    s.send("TRUNK", RPTL + T1, T1_ADDR)
    assert s.transports["TRUNK"].sent == [(MSTNAK + T1, T1_ADDR)]
    assert T1 not in s.protocols["TRUNK"]._peers
    assert any("refused: Tier III trunking system" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("packet", [RPTK + T1 + b"\x00" * 32, RPTPING + T1])
def test_classic_steps_never_reach_a_logged_in_site(packet: bytes) -> None:
    s = TrunkStack()
    s.login("TRUNK", T1, T1_ADDR)
    s.send("TRUNK", packet, T1_ADDR)
    assert s.transports["TRUNK"].sent == [(MSTNAK + T1, T1_ADDR)]
    peer = s.protocols["TRUNK"]._peers[T1]
    assert peer["CONNECTION"] == "YES" and peer["PINGS_RECEIVED"] == 0


def test_dtcg_updates_the_position_quietly(caplog) -> None:
    s = TrunkStack()
    s.login("TRUNK", T1, T1_ADDR)
    caplog.set_level(logging.INFO)
    caplog.clear()
    s.send("TRUNK", DTCG + T1 + b"+38.3452-000.4810", T1_ADDR)              # 25 bytes
    peer = s.protocols["TRUNK"]._peers[T1]
    assert (peer["LATITUDE"], peer["LONGITUDE"]) == (b"+38.3452", b"-000.4810")
    assert s.transports["TRUNK"].sent == [] and not caplog.records


@pytest.mark.parametrize("command", [DMRG, RPTG])
def test_radio_and_home_positions_are_ignored_without_errors(command: bytes, caplog) -> None:
    s = TrunkStack()
    s.login("TRUNK", T1, T1_ADDR)
    caplog.set_level(logging.INFO)
    caplog.clear()
    s.send("TRUNK", command + T1 + b"+38.3452-000.4810", T1_ADDR)
    assert s.transports["TRUNK"].sent == [] and not caplog.records


def test_classic_master_ignores_dtc_opcodes_as_before(caplog) -> None:
    """Unchanged behaviour: a MASTER without TRUNKING does not answer DTC* (it logs it)."""
    s = TrunkStack(trunking=(), classic=("MASTER-A",))
    caplog.set_level(logging.ERROR)
    s.send("MASTER-A", DTCL + T1, T1_ADDR)
    s.send("MASTER-A", dmrtc.create_registration_message(2140501, 1_700_000_000), T1_ADDR)
    assert s.transports["MASTER-A"].sent == []
    assert T1 not in s.protocols["MASTER-A"]._peers
    assert [r.getMessage().split(".")[0] for r in caplog.records] == ["(MASTER-A) Unrecognized command"] * 2


def test_classic_master_login_still_answers_with_classic_names() -> None:
    s = TrunkStack(trunking=(), classic=("MASTER-A",))
    t = s.transports["MASTER-A"]
    s.send("MASTER-A", RPTL + T1, T1_ADDR)
    assert t.sent[-1][0][:6] == RPTACK
    s.send("MASTER-A", RPTK + T1 + hashlib.sha256(t.sent[-1][0][6:10] + PASS).digest(), T1_ADDR)
    assert t.sent[-1] == (RPTACK + T1, T1_ADDR)
    assert "TRUNKING" not in s.protocols["MASTER-A"]._peers[T1]


def test_timeout_sends_dtccl_and_drops_the_site_state() -> None:
    s = TrunkStack()
    s.login("TRUNK", T1, T1_ADDR)
    s.send("TRUNK", dmrtc.create_registration_message(2140501, int(time.time())), T1_ADDR)
    s.send("TRUNK", dmrtc.create_group_subscription_message([91]), T1_ADDR)
    assert s.registry.location(2140501) == ("TRUNK", T1)
    p = s.protocols["TRUNK"]
    p._peers[T1]["LAST_PING"] = 0
    s.transports["TRUNK"].clear()

    p._master_maintenance_loop()

    assert s.transports["TRUNK"].sent == [(DTCCL + T1, T1_ADDR)]
    assert T1 not in p._peers
    assert s.registry.peer_tgs("TRUNK", T1) == []
    assert s.registry.location(2140501) == ("TRUNK", T1)  # dmrtc does not register it again on its return


def test_maintenance_loop_trims_the_registry() -> None:
    s = TrunkStack()
    trims: list[float] = []
    s.registry.trim = trims.append
    s.protocols["TRUNK"]._master_maintenance_loop()
    assert len(trims) == 1


def test_classic_master_timeout_still_sends_mstcl() -> None:
    s = TrunkStack(trunking=(), classic=("MASTER-A",))
    p = s.protocols["MASTER-A"]
    p._peers[T1] = {"CONNECTION": "YES", "LAST_PING": 0, "SOCKADDR": T1_ADDR, "CALLSIGN": b"", "RADIO_ID": "1"}
    p._master_maintenance_loop()
    assert s.transports["MASTER-A"].sent == [(MSTCL + T1, T1_ADDR)]


def test_dereg_sends_dtccl_to_sites() -> None:
    s = TrunkStack()
    s.login("TRUNK", T1, T1_ADDR)
    s.login("TRUNK", T2, T2_ADDR)
    s.protocols["TRUNK"].dereg()
    assert sorted(s.transports["TRUNK"].sent) == sorted([(DTCCL + T1, T1_ADDR), (DTCCL + T2, T2_ADDR)])


def test_relogin_drops_the_previous_session_state() -> None:
    s = TrunkStack()
    s.login("TRUNK", T1, T1_ADDR)
    s.send("TRUNK", dmrtc.create_group_subscription_message([91, 92]), T1_ADDR)
    assert s.registry.peer_tgs("TRUNK", T1) == [91, 92]
    s.send("TRUNK", DTCL + T1, T1_ADDR)                                      # the gateway restarted
    assert s.registry.peer_tgs("TRUNK", T1) == []


def test_site_login_does_not_restore_dynamic_tgs() -> None:
    dynamic = MagicMock()
    s = TrunkStack(dynamic_tg_uc=dynamic)
    s.login("TRUNK", T1, T1_ADDR)
    dynamic.restore_peer.assert_not_called()
