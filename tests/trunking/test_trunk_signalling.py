"""TrunkingUseCases: what the server answers to each DMRT message from a Tier III site.

Messages are built as dmrtc builds them and the answers are read with dmrtc's
own parsers (``tests/support/dmrtc_wire.py``). No Twisted: the sites are a
fake ``send_message`` / ``is_peer_connected`` pair.
"""

from __future__ import annotations

import logging

import pytest

from adn_server.application.trunking import NoopTrunkSubscriptionPort, TrunkingRegistry, TrunkingUseCases
from adn_server.application.trunking.registry import PENDING_TTL
from adn_server.application.trunking.use_cases import MAX_TGS_PER_SITE
from adn_server.domain import bytes_3, bytes_4
from adn_server.domain.value_objects import ID_MAX
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.config_loader import acl_build
from tests.support import dmrtc_wire as dmrtc

T1, T2, T3, T4 = (bytes_4(n) for n in (2149001, 2149002, 2149003, 2149004))
MS, MS2 = 2140501, 2140502
UUID_A, UUID_B = bytes(range(0xA0, 0xB0)), bytes(range(0xB0, 0xC0))
NOW = 1_700_000_000.0


class Sites:
    """Connected trunking sites: what each one was sent."""

    def __init__(self, connected) -> None:
        self.connected = set(connected)
        self.sent: list[tuple[str, bytes, bytes]] = []

    def send(self, system: str, peer_id: bytes, payload: bytes) -> bool:
        if (system, peer_id) not in self.connected:
            return False
        self.sent.append((system, peer_id, payload))
        return True

    def is_connected(self, system: str, peer_id: bytes) -> bool:
        return (system, peer_id) in self.connected

    def to(self, system: str, peer_id: bytes) -> list[bytes]:
        return [p for s, peer, p in self.sent if (s, peer) == (system, peer_id)]

    def last(self, system: str, peer_id: bytes) -> bytes:
        got = self.to(system, peer_id)
        assert got, f"nothing sent to {system}/{peer_id.hex()}"
        return got[-1]


class Port:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def trunk_tg_added(self, system: str, tg: int) -> None:
        self.calls.append(("added", system, tg))

    def trunk_tg_removed(self, system: str, tg: int) -> None:
        self.calls.append(("removed", system, tg))

    def trunk_resync(self, system: str) -> None:
        self.calls.append(("resync", system, 0))


def _acls(use_acl: bool = True, sub: str = "DENY:1", tg1: str = "PERMIT:ALL", tg2: str = "PERMIT:ALL") -> dict:
    return {
        "USE_ACL": use_acl,
        "SUB_ACL": acl_build(sub, ID_MAX),
        "TG1_ACL": acl_build(tg1, ID_MAX),
        "TG2_ACL": acl_build(tg2, ID_MAX),
    }


class Stack:
    def __init__(self, *, connected=(("TRUNK", T1), ("TRUNK", T2), ("TRUNK-B", T3)), glob=None, trunk=None) -> None:
        self.config = {
            "GLOBAL": glob if glob is not None else _acls(),
            "SYSTEMS": {
                "TRUNK": {"MODE": "MASTER", "TRUNKING": True, **(trunk if trunk is not None else _acls())},
                "TRUNK-B": {"MODE": "MASTER", "TRUNKING": True, **_acls()},
            },
        }
        self.registry = TrunkingRegistry()
        self.sites = Sites(connected)
        self.port = Port()
        self.sub_map: dict[int, tuple[str, bytes, float]] = {}
        self.uc = TrunkingUseCases(
            self.registry,
            get_config=lambda: self.config,
            acl_check=InMemoryAclRouter().acl_check,
            send_message=self.sites.send,
            is_peer_connected=self.sites.is_connected,
            subscription_port=self.port,
            sub_map_set=lambda ms, system, peer, t: self.sub_map.__setitem__(ms, (system, peer, t)),
            sub_map_clear=self._sub_map_clear,
        )

    def _sub_map_clear(self, ms: int, system: str, peer_id: bytes) -> None:
        if self.sub_map.get(ms, ())[:2] == (system, peer_id):
            del self.sub_map[ms]

    def dmrt(self, peer_id: bytes, payload: bytes, *, system: str = "TRUNK", now: float = NOW) -> None:
        self.uc.handle_dmrt(system, peer_id, payload, now)


def _subscribe(stack: Stack, peer_id: bytes, tgs: list[int], system: str = "TRUNK") -> None:
    stack.dmrt(peer_id, dmrtc.create_group_subscription_message(tgs), system=system)


# --- 0x01 / 0x02 registration ------------------------------------------------

@pytest.mark.unit
def test_registration_accepted_and_located() -> None:
    s = Stack()
    s.dmrt(T1, dmrtc.create_registration_message(MS, int(NOW)))
    reply = s.sites.last("TRUNK", T1)
    assert reply[4] == 0x41 and dmrtc.validate_net_message(reply)
    assert dmrtc.parse_registration_confirmation_message(reply) == (MS, True)
    assert int.from_bytes(reply[5:13], "little") == int(NOW)
    assert s.registry.location(MS) == ("TRUNK", T1)
    assert s.sub_map[MS] == ("TRUNK", T1, NOW)
    assert len(s.sites.sent) == 1


@pytest.mark.unit
@pytest.mark.parametrize(("glob", "trunk", "ms"), [
    (_acls(sub="DENY:1"), _acls(), 1),                                    # GLOBAL SUB_ACL
    (_acls(), _acls(sub="DENY:2140000-2149999"), MS),                     # system SUB_ACL
    (_acls(sub="PERMIT:3100000-3199999"), _acls(), MS),                   # GLOBAL permit-list
    (_acls(), _acls(), 0),                                                # id 0 never
])
def test_registration_denied(glob: dict, trunk: dict, ms: int) -> None:
    s = Stack(glob=glob, trunk=trunk)
    s.dmrt(T1, dmrtc.create_registration_message(ms, int(NOW)))
    assert dmrtc.parse_registration_confirmation_message(s.sites.last("TRUNK", T1)) == (ms, False)
    assert s.registry.location(ms) is None and ms not in s.sub_map


@pytest.mark.unit
def test_acl_ignored_where_use_acl_is_off() -> None:
    s = Stack(glob=_acls(use_acl=False, sub="DENY:ALL"), trunk=_acls(use_acl=False, sub="DENY:ALL"))
    s.dmrt(T1, dmrtc.create_registration_message(MS, int(NOW)))
    assert dmrtc.parse_registration_confirmation_message(s.sites.last("TRUNK", T1)) == (MS, True)


@pytest.mark.unit
def test_registration_of_several_addresses_answers_once() -> None:
    s = Stack()
    raw = b"DMRT\x01" + int(NOW).to_bytes(8, "little") + b"\x03" + bytes_3(MS) + bytes_3(1) + bytes_3(MS2)
    s.dmrt(T1, raw)
    assert len(s.sites.sent) == 1
    reply = s.sites.last("TRUNK", T1)
    assert reply[13] == 0b101 and reply[14] == 3 and reply[15:] == bytes_3(MS) + bytes_3(1) + bytes_3(MS2)
    assert s.registry.registrations_at("TRUNK", T1) == [MS, MS2]


@pytest.mark.unit
@pytest.mark.parametrize(("new_system", "new_peer"), [("TRUNK", T2), ("TRUNK-B", T3)])
def test_roaming_deregisters_the_ms_at_the_old_site(new_system: str, new_peer: bytes) -> None:
    s = Stack()
    s.dmrt(T1, dmrtc.create_registration_message(MS, int(NOW)))
    s.sites.sent.clear()
    s.dmrt(new_peer, dmrtc.create_registration_message(MS, int(NOW) + 5), system=new_system, now=NOW + 5)
    assert dmrtc.parse_registration_confirmation_message(s.sites.last(new_system, new_peer)) == (MS, True)
    old = s.sites.to("TRUNK", T1)
    assert len(old) == 1 and old[0][4] == 0x42
    assert dmrtc.parse_deregistration_confirmation_message(old[0]) == MS
    assert s.registry.location(MS) == (new_system, new_peer)
    assert s.sub_map[MS] == (new_system, new_peer, NOW + 5)


@pytest.mark.unit
def test_roaming_from_a_disconnected_site_sends_nothing_there() -> None:
    s = Stack()
    s.dmrt(T1, dmrtc.create_registration_message(MS, int(NOW)))
    s.sites.connected.discard(("TRUNK", T1))
    s.sites.sent.clear()
    s.dmrt(T2, dmrtc.create_registration_message(MS, int(NOW)))
    assert [peer for _s, peer, _p in s.sites.sent] == [T2]


@pytest.mark.unit
def test_registering_again_at_the_same_site_sends_no_deregistration() -> None:
    s = Stack()
    s.dmrt(T1, dmrtc.create_registration_message(MS, int(NOW)))
    s.dmrt(T1, dmrtc.create_registration_message(MS, int(NOW)))
    assert [p[4] for p in s.sites.to("TRUNK", T1)] == [0x41, 0x41]


@pytest.mark.unit
def test_deregistration_forgets_the_ms_and_is_echoed() -> None:
    s = Stack()
    s.dmrt(T1, dmrtc.create_registration_message(MS, int(NOW)))
    s.dmrt(T1, dmrtc.create_deregistration_message(MS, int(NOW)))
    reply = s.sites.last("TRUNK", T1)
    assert reply[4] == 0x42 and dmrtc.parse_deregistration_confirmation_message(reply) == MS
    assert s.registry.location(MS) is None and MS not in s.sub_map


@pytest.mark.unit
def test_deregistration_from_another_site_keeps_the_ms_where_it_is() -> None:
    s = Stack()
    s.dmrt(T2, dmrtc.create_registration_message(MS, int(NOW)))
    s.dmrt(T1, dmrtc.create_deregistration_message(MS, int(NOW)))      # stale: MS moved to T2
    assert dmrtc.parse_deregistration_confirmation_message(s.sites.last("TRUNK", T1)) == MS
    assert s.registry.location(MS) == ("TRUNK", T2) and s.sub_map[MS][:2] == ("TRUNK", T2)


# --- 0x03 / 0x04 group subscriptions -------------------------------------------

@pytest.mark.unit
def test_subscription_confirmed_and_routing_told_once_per_system() -> None:
    s = Stack()
    _subscribe(s, T1, [91, 214])
    reply = s.sites.last("TRUNK", T1)
    assert reply[4] == 0x43 and dmrtc.validate_net_message(reply)
    assert dmrtc.parse_subscription_confirmation_message(reply) == [91, 214]
    _subscribe(s, T2, [91])                                              # second holder on TRUNK
    _subscribe(s, T3, [91], system="TRUNK-B")                            # first holder on TRUNK-B
    assert s.port.calls == [("added", "TRUNK", 91), ("added", "TRUNK", 214), ("added", "TRUNK-B", 91)]
    assert s.registry.subscribers("TRUNK", 91) == sorted([T1, T2])


@pytest.mark.unit
def test_invalid_talkgroups_are_refused_with_zero() -> None:
    s = Stack()
    _subscribe(s, T1, [0, 9, 91, 4000, 5000, 9990, 9999])
    assert dmrtc.parse_subscription_confirmation_message(s.sites.last("TRUNK", T1)) == [0, 0, 91, 0, 0, 0, 0]
    assert s.port.calls == [("added", "TRUNK", 91)]
    assert s.registry.peer_tgs("TRUNK", T1) == [91]


@pytest.mark.unit
@pytest.mark.parametrize(("glob", "trunk", "permitted"), [
    (_acls(tg1="DENY:ALL"), _acls(), True),                              # TS2 permits in both scopes
    (_acls(tg1="DENY:ALL", tg2="DENY:ALL"), _acls(), False),
    (_acls(tg1="DENY:ALL"), _acls(tg2="DENY:91"), False),                # TS1 global, TS2 system
    (_acls(tg2="DENY:91"), _acls(tg1="DENY:91"), False),
    (_acls(tg2="DENY:91"), _acls(tg2="DENY:91"), True),                  # TS1 in both scopes
    (_acls(use_acl=False, tg1="DENY:ALL", tg2="DENY:ALL"), _acls(), True),
])
def test_subscription_talkgroup_acls(glob: dict, trunk: dict, permitted: bool) -> None:
    s = Stack(glob=glob, trunk=trunk)
    _subscribe(s, T1, [91])
    assert dmrtc.parse_subscription_confirmation_message(s.sites.last("TRUNK", T1)) == [91 if permitted else 0]
    assert s.registry.is_subscribed("TRUNK", T1, 91) is permitted


@pytest.mark.unit
def test_a_site_holds_at_most_512_talkgroups() -> None:
    s = Stack()
    for tg in range(100, 100 + MAX_TGS_PER_SITE):
        s.registry.subscribe("TRUNK", T1, tg)
    _subscribe(s, T1, [50, 100])                                         # new one refused, held one kept
    assert dmrtc.parse_subscription_confirmation_message(s.sites.last("TRUNK", T1)) == [0, 100]
    assert s.registry.peer_tg_count("TRUNK", T1) == MAX_TGS_PER_SITE and s.port.calls == []


@pytest.mark.unit
def test_unsubscription_echoes_every_tg_and_releases_the_last_holder() -> None:
    s = Stack()
    _subscribe(s, T1, [91, 92])
    _subscribe(s, T2, [92])
    s.port.calls.clear()
    s.dmrt(T1, dmrtc.create_group_unsubscription_message([91, 92, 93]))
    reply = s.sites.last("TRUNK", T1)
    assert reply[4] == 0x44 and dmrtc.parse_subscription_confirmation_message(reply) == [91, 92, 93]
    assert s.port.calls == [("removed", "TRUNK", 91)]                    # 92 still held by T2
    assert s.registry.peer_tgs("TRUNK", T1) == []
    s.dmrt(T2, dmrtc.create_group_unsubscription_message([92]))
    assert s.port.calls[-1] == ("removed", "TRUNK", 92)


# --- 0x05 / 0x06 UDT -----------------------------------------------------------

@pytest.mark.unit
def test_group_udt_reaches_every_other_subscribed_site_on_any_system() -> None:
    s = Stack(connected=(("TRUNK", T1), ("TRUNK", T2), ("TRUNK-B", T3)))
    for system, peer in (("TRUNK", T1), ("TRUNK", T2), ("TRUNK-B", T3), ("TRUNK", T4)):
        s.registry.subscribe(system, peer, 91)                           # T4 subscribed but gone
    s.registry.subscribe("TRUNK", T2, 92)
    raw = dmrtc.create_udt_transfer_message(MS, 91, b"hello all", 4, True, UUID_A)
    s.sites.sent.clear()
    s.dmrt(T1, raw)
    assert sorted((sys_name, peer) for sys_name, peer, _p in s.sites.sent) == [("TRUNK", T2), ("TRUNK-B", T3)]
    for _sys, _peer, out in s.sites.sent:
        assert out[4] == 0x45 and out[5:] == raw[5:] and dmrtc.validate_net_message(out)
        parsed = dmrtc.parse_udt_transfer_message(out)
        assert (parsed["group"], parsed["src"], parsed["dst"], parsed["message"]) == (True, MS, 91, b"hello all")


@pytest.mark.unit
def test_udt_accepts_go_back_to_the_sender() -> None:
    s = Stack()
    s.registry.subscribe("TRUNK", T2, 91)
    s.registry.subscribe("TRUNK-B", T3, 91)
    s.dmrt(T1, dmrtc.create_udt_transfer_message(MS, 91, b"hi", 4, True, UUID_A))
    s.sites.sent.clear()
    accept = dmrtc.create_udt_accept_message(91, MS, UUID_A)
    s.dmrt(T2, accept, now=NOW + 1)
    s.dmrt(T3, accept, system="TRUNK-B", now=NOW + 2)                   # one accept per site
    back = s.sites.to("TRUNK", T1)
    assert len(back) == 2 and len(s.sites.sent) == 2
    assert all(b[4] == 0x46 and b[5:] == accept[5:] for b in back)
    assert dmrtc.parse_udt_accept_message(back[0]) == (91, MS, UUID_A)


@pytest.mark.unit
def test_private_udt_goes_to_the_site_of_the_destination() -> None:
    s = Stack()
    s.registry.register(MS2, "TRUNK-B", T3, NOW)
    s.registry.subscribe("TRUNK", T2, MS2)                               # never a group target here
    raw = dmrtc.create_udt_transfer_message(MS, MS2, b"psst", 3, False, UUID_A)
    s.dmrt(T1, raw)
    assert [(sys_name, peer) for sys_name, peer, _p in s.sites.sent] == [("TRUNK-B", T3)]
    assert s.sites.last("TRUNK-B", T3) == b"DMRT\x45" + raw[5:]
    s.dmrt(T3, dmrtc.create_udt_accept_message(MS2, MS, UUID_A), system="TRUNK-B")
    assert s.sites.last("TRUNK", T1)[4] == 0x46


@pytest.mark.unit
@pytest.mark.parametrize("where", [None, ("TRUNK", T1), ("TRUNK", T4)])
def test_private_udt_with_no_destination_site_goes_nowhere(where) -> None:
    s = Stack()
    if where is not None:
        s.registry.register(MS2, *where, NOW)                            # at the sender / disconnected
    s.dmrt(T1, dmrtc.create_udt_transfer_message(MS, MS2, b"psst", 3, False, UUID_A))
    assert s.sites.sent == []
    s.dmrt(T2, dmrtc.create_udt_accept_message(MS2, MS, UUID_A))       # nothing pending: dropped
    assert s.sites.sent == []


@pytest.mark.unit
def test_udt_accept_for_unknown_or_expired_uuid_is_dropped() -> None:
    s = Stack()
    s.registry.subscribe("TRUNK", T2, 91)
    s.dmrt(T1, dmrtc.create_udt_transfer_message(MS, 91, b"hi", 4, True, UUID_A))
    s.sites.sent.clear()
    s.dmrt(T2, dmrtc.create_udt_accept_message(91, MS, UUID_B))
    s.dmrt(T2, dmrtc.create_udt_accept_message(91, MS, UUID_A), now=NOW + PENDING_TTL + 1)
    assert s.sites.sent == []


@pytest.mark.unit
def test_udt_accept_to_an_origin_that_left_is_dropped() -> None:
    s = Stack()
    s.registry.subscribe("TRUNK", T2, 91)
    s.dmrt(T1, dmrtc.create_udt_transfer_message(MS, 91, b"hi", 4, True, UUID_A))
    s.sites.sent.clear()
    s.sites.connected.discard(("TRUNK", T1))
    s.dmrt(T2, dmrtc.create_udt_accept_message(91, MS, UUID_A))
    assert s.sites.sent == []


# --- 0x07 / 0x08 / 0x09 private call setup ---------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize(("accept", "answer"), [(True, 0x48), (False, 0x49)])
def test_private_call_setup_and_answer(accept: bool, answer: int) -> None:
    s = Stack()
    s.registry.register(MS2, "TRUNK", T2, NOW)
    setup = dmrtc.create_private_call_setup_message(MS, MS2, 0x20, UUID_A)
    s.dmrt(T1, setup)
    assert s.sites.sent == [("TRUNK", T2, b"DMRT\x47" + setup[5:])]
    reply = dmrtc.create_private_call_reply_message(MS2, MS, UUID_A, accept)
    s.dmrt(T2, reply)
    assert s.sites.last("TRUNK", T1) == bytes((*b"DMRT", answer)) + reply[5:]


@pytest.mark.unit
@pytest.mark.parametrize("where", [None, ("TRUNK", T1), ("TRUNK", T4)])
def test_private_call_setup_to_an_unreachable_ms_is_rejected(where) -> None:
    s = Stack()
    if where is not None:
        s.registry.register(MS2, *where, NOW)
    s.dmrt(T1, dmrtc.create_private_call_setup_message(MS, MS2, 0x20, UUID_A))
    assert s.sites.sent == [("TRUNK", T1, b"DMRT\x49" + UUID_A + bytes_3(MS) + bytes_3(MS2))]


@pytest.mark.unit
def test_call_answer_does_not_follow_a_udt_uuid() -> None:
    s = Stack()
    s.registry.subscribe("TRUNK", T2, 91)
    s.dmrt(T1, dmrtc.create_udt_transfer_message(MS, 91, b"hi", 4, True, UUID_A))
    s.sites.sent.clear()
    s.dmrt(T2, dmrtc.create_private_call_reply_message(91, MS, UUID_A, True))
    assert s.sites.sent == []


# --- 0x0A status ---------------------------------------------------------------

@pytest.mark.unit
def test_group_status_goes_to_subscribed_sites_except_the_sender() -> None:
    s = Stack()
    for system, peer in (("TRUNK", T1), ("TRUNK", T2), ("TRUNK-B", T3)):
        s.registry.subscribe(system, peer, 91)
    raw = dmrtc.create_status_transfer_message(MS, 91, 5, True, UUID_A)
    s.dmrt(T1, raw)
    assert sorted((sys_name, peer, p) for sys_name, peer, p in s.sites.sent) == [
        ("TRUNK", T2, b"DMRT\x4a" + raw[5:]), ("TRUNK-B", T3, b"DMRT\x4a" + raw[5:]),
    ]
    assert s.registry.pending(UUID_A, NOW) is None


@pytest.mark.unit
def test_private_status_goes_to_the_registered_site() -> None:
    s = Stack()
    s.registry.register(MS2, "TRUNK", T2, NOW)
    s.dmrt(T1, dmrtc.create_status_transfer_message(MS, MS2, 5, False, UUID_A))
    assert [(sys_name, peer) for sys_name, peer, _p in s.sites.sent] == [("TRUNK", T2)]


# --- malformed / not for the server ------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("raw", [
    b"DMRT", b"DMRT\x01\x00", b"DMRT\x41" + b"\x00" * 13, b"DMRT\xc1", b"DMRT\x81\x01",
    b"DMRT\x0b" + b"\x00" * 30, b"DMRT\x03\x05" + bytes_3(91), b"DMRT\x03" + b"\x00" * 251,
])
def test_malformed_or_foreign_opcodes_are_dropped_quietly(raw: bytes) -> None:
    s = Stack()
    s.dmrt(T1, raw)
    assert s.sites.sent == [] and s.port.calls == []


@pytest.mark.unit
def test_a_dropped_opcode_is_logged_once_per_site(caplog: pytest.LogCaptureFixture) -> None:
    s = Stack()
    with caplog.at_level(logging.DEBUG, logger="adn_server.application.trunking.use_cases"):
        for _ in range(3):
            s.dmrt(T1, b"DMRT\x81\x01")
        s.dmrt(T2, b"DMRT\x81\x01")
        s.dmrt(T1, b"DMRT")
    drops = [r for r in caplog.records if "not accepted" in r.getMessage()]
    assert len(drops) == 3
    assert all(r.levelno == logging.DEBUG for r in drops)
    s.uc.peer_disconnected("TRUNK", T1)                                  # a new session logs again
    with caplog.at_level(logging.DEBUG, logger="adn_server.application.trunking.use_cases"):
        s.dmrt(T1, b"DMRT\x81\x01")
    assert len([r for r in caplog.records if "not accepted" in r.getMessage()]) == 4


# --- lifecycle -------------------------------------------------------------------

@pytest.mark.unit
def test_peer_disconnected_drops_the_site_and_releases_its_talkgroups() -> None:
    s = Stack()
    s.dmrt(T1, dmrtc.create_registration_message(MS, int(NOW)))
    _subscribe(s, T1, [91, 92])
    _subscribe(s, T2, [92])
    s.registry.subscribe("TRUNK-B", T3, 91)
    s.dmrt(T1, dmrtc.create_udt_transfer_message(MS, 92, b"hi", 4, True, UUID_A))
    s.port.calls.clear()
    s.uc.peer_disconnected("TRUNK", T1)
    assert s.port.calls == [("removed", "TRUNK", 91)]
    assert s.registry.peer_tgs("TRUNK", T1) == []
    assert s.registry.location(MS) == ("TRUNK", T1)  # dmrtc does not register it again on its return
    assert s.registry.subscribers("TRUNK-B", 91) == [T3]
    s.sites.sent.clear()
    s.dmrt(T2, dmrtc.create_udt_accept_message(92, MS, UUID_A))        # origin gone with its pendings
    assert s.sites.sent == []


@pytest.mark.unit
def test_system_removed_forgets_its_sites() -> None:
    s = Stack()
    s.dmrt(T1, dmrtc.create_registration_message(MS, int(NOW)))
    _subscribe(s, T1, [91])
    _subscribe(s, T2, [91, 92])
    _subscribe(s, T3, [91], system="TRUNK-B")
    s.port.calls.clear()
    s.uc.system_removed("TRUNK")
    assert s.port.calls == [("removed", "TRUNK", 91), ("removed", "TRUNK", 92)]  # its routing legs go too
    assert s.registry.location(MS) is None and s.registry.system_tgs("TRUNK") == []
    assert s.registry.system_tgs("TRUNK-B") == [91]


@pytest.mark.unit
def test_works_without_optional_collaborators() -> None:
    sites = Sites((("TRUNK", T1),))
    uc = TrunkingUseCases(
        TrunkingRegistry(), get_config=lambda: {}, acl_check=InMemoryAclRouter().acl_check,
        send_message=sites.send, is_peer_connected=sites.is_connected,
    )
    uc.handle_dmrt("TRUNK", T1, dmrtc.create_registration_message(MS, int(NOW)), NOW)
    uc.handle_dmrt("TRUNK", T1, dmrtc.create_group_subscription_message([91]), NOW)
    uc.peer_disconnected("TRUNK", T1)
    assert [p[4] for p in sites.to("TRUNK", T1)] == [0x41, 0x43]
    assert dmrtc.parse_registration_confirmation_message(sites.to("TRUNK", T1)[0]) == (MS, True)


@pytest.mark.unit
def test_noop_subscription_port() -> None:
    port = NoopTrunkSubscriptionPort()
    assert port.trunk_tg_added("TRUNK", 91) is None
    assert port.trunk_tg_removed("TRUNK", 91) is None
    assert port.trunk_resync("TRUNK") is None
