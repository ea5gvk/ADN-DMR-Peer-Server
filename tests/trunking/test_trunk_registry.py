"""TrunkingRegistry: MS locations, per-site talkgroups, stream UUIDs, pendings and echo sources."""

from __future__ import annotations

import pytest

from adn_server.application.trunking.registry import (
    ECHO_TTL,
    PENDING_PRIVATE_CALL,
    PENDING_TTL,
    PENDING_UDT,
    STREAM_UUID_IDLE,
    TRIM_INTERVAL,
    TrunkingRegistry,
)
from adn_server.domain import bytes_4
from adn_server.domain.trunking import derive_stream_uuid

T1, T2, T3 = bytes_4(2149001), bytes_4(2149002), bytes_4(2149003)
MS = 2140501
UUID_A = bytes(range(0xA0, 0xB0))
STREAM = b"\x0a\x0b\x0c\x0d"
NOW = 1_700_000_000.0


# --- registrations ----------------------------------------------------------

@pytest.mark.unit
def test_register_then_roam_reports_the_old_site() -> None:
    reg = TrunkingRegistry()
    assert reg.register(MS, "TRUNK", T1, NOW) is None
    assert reg.register(MS, "TRUNK", T1, NOW + 1) is None              # same site again
    assert reg.register(MS, "TRUNK", T2, NOW + 2) == ("TRUNK", T1)
    assert reg.location(MS) == ("TRUNK", T2)
    assert reg.register(MS, "TRUNK-B", T2, NOW + 3) == ("TRUNK", T2)  # same peer id, other system
    assert reg.location(MS) == ("TRUNK-B", T2)


@pytest.mark.unit
def test_deregister_only_from_the_site_it_is_at() -> None:
    reg = TrunkingRegistry()
    reg.register(MS, "TRUNK", T2, NOW)
    assert reg.deregister(MS, "TRUNK", T1) is False
    assert reg.location(MS) == ("TRUNK", T2)
    assert reg.deregister(MS, "TRUNK", T2) is True
    assert reg.location(MS) is None
    assert reg.deregister(MS, "TRUNK", T2) is False


@pytest.mark.unit
def test_registrations_at_a_site() -> None:
    reg = TrunkingRegistry()
    reg.register(MS + 1, "TRUNK", T1, NOW)
    reg.register(MS, "TRUNK", T1, NOW)
    reg.register(MS + 2, "TRUNK", T2, NOW)
    assert reg.registrations_at("TRUNK", T1) == [MS, MS + 1]


# --- subscriptions ----------------------------------------------------------

@pytest.mark.unit
def test_first_and_last_holder_per_system() -> None:
    reg = TrunkingRegistry()
    assert reg.subscribe("TRUNK", T1, 91) is True                       # first holder on TRUNK
    assert reg.subscribe("TRUNK", T1, 91) is False                      # already held
    assert reg.subscribe("TRUNK", T2, 91) is False                      # second holder
    assert reg.subscribe("TRUNK-B", T3, 91) is True                     # first holder on TRUNK-B
    assert reg.holders_count("TRUNK", 91) == 2
    assert reg.subscribers("TRUNK", 91) == sorted([T1, T2])
    assert reg.all_subscriber_peers(91) == [("TRUNK", T1), ("TRUNK", T2), ("TRUNK-B", T3)]
    assert reg.unsubscribe("TRUNK", T1, 91) is False                    # T2 still holds it
    assert reg.unsubscribe("TRUNK", T1, 91) is False                    # not held any more
    assert reg.unsubscribe("TRUNK", T2, 91) is True                     # last holder on TRUNK
    assert reg.subscribers("TRUNK", 91) == [] and reg.holders_count("TRUNK", 91) == 0
    assert reg.all_subscriber_peers(91) == [("TRUNK-B", T3)]


@pytest.mark.unit
def test_per_site_talkgroups_have_no_slot() -> None:
    reg = TrunkingRegistry()
    for tg in (214, 91, 92):
        reg.subscribe("TRUNK", T1, tg)
    assert reg.peer_tgs("TRUNK", T1) == [91, 92, 214]
    assert reg.peer_tg_count("TRUNK", T1) == 3
    assert reg.is_subscribed("TRUNK", T1, 92) and not reg.is_subscribed("TRUNK", T2, 92)
    assert reg.system_tgs("TRUNK") == [91, 92, 214] and reg.system_tgs("TRUNK-B") == []
    assert reg.subscribers("TRUNK", 93) == [] and reg.all_subscriber_peers(93) == []


# --- stream UUIDs -----------------------------------------------------------

@pytest.mark.unit
def test_stream_uuid_from_the_originating_site_else_derived() -> None:
    reg = TrunkingRegistry()
    assert reg.uuid_for_stream(STREAM) == derive_stream_uuid(STREAM)
    reg.note_stream_uuid(STREAM, UUID_A, NOW)
    assert reg.uuid_for_stream(STREAM) == UUID_A


@pytest.mark.unit
@pytest.mark.parametrize("uuid", [b"\x00" * 16, b"\x01" * 15, b""])
def test_stream_uuid_ignores_zero_or_short_uuid(uuid: bytes) -> None:
    reg = TrunkingRegistry()
    reg.note_stream_uuid(STREAM, uuid, NOW)
    assert reg.uuid_for_stream(STREAM) == derive_stream_uuid(STREAM)


@pytest.mark.unit
def test_stream_uuid_trimmed_after_idle() -> None:
    reg = TrunkingRegistry()
    reg.note_stream_uuid(STREAM, UUID_A, NOW)
    reg.trim(NOW + STREAM_UUID_IDLE)
    assert reg.uuid_for_stream(STREAM) == UUID_A                         # not idle long enough
    reg.note_stream_uuid(STREAM, UUID_A, NOW + 50)                       # a frame refreshes it
    reg.trim(NOW + 50 + STREAM_UUID_IDLE + 1)
    assert reg.uuid_for_stream(STREAM) == derive_stream_uuid(STREAM)


@pytest.mark.unit
def test_registry_trims_itself_as_it_is_fed() -> None:
    reg = TrunkingRegistry()
    reg.note_stream_uuid(STREAM, UUID_A, NOW)
    reg.note_pending(UUID_A, PENDING_UDT, "TRUNK", T1, NOW)
    later = NOW + STREAM_UUID_IDLE + TRIM_INTERVAL
    reg.note_stream_uuid(b"\x01\x02\x03\x04", bytes(range(16, 32)), later)   # triggers the trim
    assert reg.uuid_for_stream(STREAM) == derive_stream_uuid(STREAM)
    assert UUID_A not in reg._pending


# --- pending transactions ---------------------------------------------------

@pytest.mark.unit
def test_pending_lives_for_its_ttl_and_survives_lookups() -> None:
    reg = TrunkingRegistry()
    reg.note_pending(UUID_A, PENDING_PRIVATE_CALL, "TRUNK", T1, NOW)
    first = reg.pending(UUID_A, NOW + 1)
    assert (first.kind, first.system, first.peer_id) == (PENDING_PRIVATE_CALL, "TRUNK", T1)
    assert reg.pending(UUID_A, NOW + PENDING_TTL) == first               # several answers allowed
    assert reg.pending(UUID_A, NOW + PENDING_TTL + 1) is None
    assert reg.pending(bytes(16), NOW) is None
    reg.trim(NOW + PENDING_TTL + 1)
    assert UUID_A not in reg._pending


# --- echo sources -----------------------------------------------------------

@pytest.mark.unit
def test_echo_peer_valid_for_a_minute_after_the_last_frame() -> None:
    reg = TrunkingRegistry()
    assert reg.echo_peer("TRUNK", 9990, NOW) is None
    reg.note_echo_source("TRUNK", 9990, T1, NOW)
    assert reg.echo_peer("TRUNK", 9990, NOW + ECHO_TTL) == T1
    assert reg.echo_peer("TRUNK-B", 9990, NOW) is None
    assert reg.echo_peer("TRUNK", 9990, NOW + ECHO_TTL + 1) is None
    reg.note_echo_source("TRUNK", 9990, T2, NOW + 100)                   # someone else keyed it
    assert reg.echo_peer("TRUNK", 9990, NOW + 101) == T2


# --- lifecycle --------------------------------------------------------------

def _populated() -> TrunkingRegistry:
    reg = TrunkingRegistry()
    reg.register(MS, "TRUNK", T1, NOW)
    reg.register(MS + 1, "TRUNK", T2, NOW)
    for tg in (91, 92, 214):
        reg.subscribe("TRUNK", T1, tg)
    reg.subscribe("TRUNK", T2, 92)
    reg.subscribe("TRUNK-B", T1, 91)                                     # same peer id, other system
    reg.note_pending(UUID_A, PENDING_UDT, "TRUNK", T1, NOW)
    reg.note_echo_source("TRUNK", 9990, T1, NOW)
    return reg


@pytest.mark.unit
def test_drop_peer_forgets_the_site_and_returns_released_tgs() -> None:
    reg = _populated()
    assert reg.drop_peer("TRUNK", T1) == [91, 214]                       # 92 still held by T2
    # dmrtc never registers its radios again after a re-login: they stay at the site
    assert reg.location(MS) == ("TRUNK", T1) and reg.location(MS + 1) == ("TRUNK", T2)
    assert reg.peer_tgs("TRUNK", T1) == [] and reg.subscribers("TRUNK", 92) == [T2]
    assert reg.subscribers("TRUNK-B", 91) == [T1]                        # other system untouched
    assert reg.pending(UUID_A, NOW) is None
    assert reg.echo_peer("TRUNK", 9990, NOW) is None
    assert reg.drop_peer("TRUNK", T1) == []


@pytest.mark.unit
def test_drop_system_forgets_every_site_of_it() -> None:
    reg = _populated()
    reg.drop_system("TRUNK")
    assert reg.location(MS) is None and reg.location(MS + 1) is None
    assert reg.system_tgs("TRUNK") == [] and reg.all_subscriber_peers(92) == []
    assert reg.all_subscriber_peers(91) == [("TRUNK-B", T1)]
    assert reg.pending(UUID_A, NOW) is None and reg.echo_peer("TRUNK", 9990, NOW) is None
