# ADN DMR Peer Server - DMR Tier III trunking registry
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

"""What the Tier III sites told the server: where each MS is, which TGs each site wants.

One registry per process, created at bootstrap and injected; it is never kept
inside the config dict (SIGHUP deep-copies that), so it survives a reload. A
site is ``(system, peer_id)``: the trunking MASTER it logged into and its
4-byte repeater ID.

- registrations: an MS is at one site at a time (the last one it registered at).
  They outlive the site's re-login: dmrtc keeps its radios registered across a
  gateway reconnect and never sends their 0x01 again;
- subscriptions: per-site talkgroups, no timeslot and no timer — they last until
  the site unsubscribes or disconnects;
- stream UUIDs: the call UUID a trunking site sent for a stream, so the other
  sites get the same one (a derived one otherwise);
- pending transactions: UDT / private-call setup UUID -> the site that asked,
  so the answer goes back there;
- echo sources: which site keyed a 9990..9999 parrot, so the playback goes back
  to it alone.

The last three expire after a minute; the registry trims them itself every
``TRIM_INTERVAL`` seconds as it is fed, and ``trim(now)`` does it on demand.
"""

from __future__ import annotations

from typing import NamedTuple

from adn_server.domain.trunking import CALL_UUID_LEN, derive_stream_uuid

STREAM_UUID_IDLE = 60.0
PENDING_TTL = 60.0
ECHO_TTL = 60.0
TRIM_INTERVAL = 30.0

PENDING_UDT = "udt"
PENDING_PRIVATE_CALL = "pc"

Site = tuple[str, bytes]


class Registration(NamedTuple):
    system: str
    peer_id: bytes
    t: float


class PendingTransaction(NamedTuple):
    kind: str
    system: str
    peer_id: bytes
    t: float


class TrunkingRegistry:
    """In-memory Tier III state for every trunking system (single reactor thread)."""

    def __init__(self) -> None:
        self._registrations: dict[int, Registration] = {}
        self._peer_tgs: dict[Site, set[int]] = {}
        self._tg_peers: dict[int, dict[str, set[bytes]]] = {}
        self._stream_uuids: dict[bytes, tuple[bytes, float]] = {}
        self._pending: dict[bytes, PendingTransaction] = {}
        self._echo: dict[tuple[str, int], tuple[bytes, float]] = {}
        self._last_trim = 0.0

    # --- registrations -------------------------------------------------

    def register(self, ms_id: int, system: str, peer_id: bytes, t: float) -> Site | None:
        """Place ``ms_id`` at this site; returns the other site it was at before, if any."""
        previous = self._registrations.get(ms_id)
        self._registrations[ms_id] = Registration(system, peer_id, t)
        if previous is None or (previous.system, previous.peer_id) == (system, peer_id):
            return None
        return (previous.system, previous.peer_id)

    def deregister(self, ms_id: int, system: str, peer_id: bytes) -> bool:
        """Forget ``ms_id`` only if it is registered at this site."""
        current = self._registrations.get(ms_id)
        if current is None or (current.system, current.peer_id) != (system, peer_id):
            return False
        del self._registrations[ms_id]
        return True

    def registration(self, ms_id: int) -> Registration | None:
        """Where ``ms_id`` registered and when, or None."""
        return self._registrations.get(ms_id)

    def location(self, ms_id: int) -> Site | None:
        """``(system, peer_id)`` where ``ms_id`` is registered, or None."""
        current = self._registrations.get(ms_id)
        return None if current is None else (current.system, current.peer_id)

    def registrations_at(self, system: str, peer_id: bytes) -> list[int]:
        return sorted(ms for ms, r in self._registrations.items() if (r.system, r.peer_id) == (system, peer_id))

    # --- subscriptions -------------------------------------------------

    def subscribe(self, system: str, peer_id: bytes, tg: int) -> bool:
        """Add ``tg`` to the site; True when it is the first site of ``system`` holding it."""
        tgs = self._peer_tgs.setdefault((system, peer_id), set())
        if tg in tgs:
            return False
        tgs.add(tg)
        peers = self._tg_peers.setdefault(tg, {}).setdefault(system, set())
        peers.add(peer_id)
        return len(peers) == 1

    def unsubscribe(self, system: str, peer_id: bytes, tg: int) -> bool:
        """Drop ``tg`` from the site; True when no site of ``system`` holds it any more."""
        tgs = self._peer_tgs.get((system, peer_id))
        if not tgs or tg not in tgs:
            return False
        tgs.discard(tg)
        if not tgs:
            del self._peer_tgs[(system, peer_id)]
        return self._release(system, peer_id, tg)

    def _release(self, system: str, peer_id: bytes, tg: int) -> bool:
        by_system = self._tg_peers.get(tg, {})
        peers = by_system.get(system)
        if peers is None:
            return False
        peers.discard(peer_id)
        if peers:
            return False
        del by_system[system]
        if not by_system:
            self._tg_peers.pop(tg, None)
        return True

    def is_subscribed(self, system: str, peer_id: bytes, tg: int) -> bool:
        return tg in self._peer_tgs.get((system, peer_id), ())

    def peer_tgs(self, system: str, peer_id: bytes) -> list[int]:
        return sorted(self._peer_tgs.get((system, peer_id), ()))

    def peer_tg_count(self, system: str, peer_id: bytes) -> int:
        return len(self._peer_tgs.get((system, peer_id), ()))

    def subscribers(self, system: str, tg: int) -> list[bytes]:
        """Sites of ``system`` subscribed to ``tg``."""
        return sorted(self._tg_peers.get(tg, {}).get(system, ()))

    def holders_count(self, system: str, tg: int) -> int:
        return len(self._tg_peers.get(tg, {}).get(system, ()))

    def all_subscriber_peers(self, tg: int) -> list[Site]:
        """Every site, on every trunking system, subscribed to ``tg``."""
        return sorted((system, peer_id) for system, peers in self._tg_peers.get(tg, {}).items() for peer_id in peers)

    def system_tgs(self, system: str) -> list[int]:
        """Talkgroups at least one site of ``system`` holds (what a resync re-applies)."""
        return sorted(tg for tg, by_system in self._tg_peers.items() if by_system.get(system))

    # --- stream UUIDs --------------------------------------------------

    def note_stream_uuid(self, stream_id: bytes, uuid: bytes, t: float) -> None:
        """Remember the call UUID a trunking site sent for ``stream_id`` (all-zero ignored)."""
        if len(uuid) != CALL_UUID_LEN or not any(uuid):
            return
        self._stream_uuids[bytes(stream_id)] = (bytes(uuid), t)
        self._maybe_trim(t)

    def uuid_for_stream(self, stream_id: bytes) -> bytes:
        """The UUID to send with ``stream_id``: the originating site's, else a derived one."""
        known = self._stream_uuids.get(bytes(stream_id))
        return known[0] if known is not None else derive_stream_uuid(stream_id)

    # --- pending transactions -------------------------------------------

    def note_pending(self, uuid: bytes, kind: str, system: str, peer_id: bytes, t: float) -> None:
        self._pending[bytes(uuid)] = PendingTransaction(kind, system, peer_id, t)
        self._maybe_trim(t)

    def pending(self, uuid: bytes, now: float) -> PendingTransaction | None:
        """Who asked for ``uuid`` (kept for the TTL: a group UDT gets one accept per site)."""
        entry = self._pending.get(bytes(uuid))
        if entry is None or now - entry.t > PENDING_TTL:
            return None
        return entry

    # --- echo talkgroups -----------------------------------------------

    def note_echo_source(self, system: str, tg: int, peer_id: bytes, t: float) -> None:
        self._echo[(system, tg)] = (peer_id, t)
        self._maybe_trim(t)

    def echo_peer(self, system: str, tg: int, now: float) -> bytes | None:
        """The site that last keyed echo ``tg`` on ``system``, for ``ECHO_TTL`` after its last frame."""
        entry = self._echo.get((system, tg))
        if entry is None or now - entry[1] > ECHO_TTL:
            return None
        return entry[0]

    # --- lifecycle -----------------------------------------------------

    def drop_peer(self, system: str, peer_id: bytes) -> list[int]:
        """A site left: forget its talkgroups, pendings and echo; returns the TGs no site of ``system`` holds any more.

        Its registrations stay: dmrtc re-subscribes on its next login but never
        registers those radios again (a disconnected site is never a target).
        """
        site = (system, peer_id)
        released = [tg for tg in sorted(self._peer_tgs.pop(site, ())) if self._release(system, peer_id, tg)]
        for uuid in [u for u, p in self._pending.items() if (p.system, p.peer_id) == site]:
            del self._pending[uuid]
        for key in [k for k, (p, _t) in self._echo.items() if k[0] == system and p == peer_id]:
            del self._echo[key]
        return released

    def drop_system(self, system: str) -> None:
        """Forget every site of ``system`` (removed, or flipped out of trunking, on reload)."""
        for ms in [ms for ms, r in self._registrations.items() if r.system == system]:
            del self._registrations[ms]
        peers = {peer_id for (sys_name, peer_id) in self._peer_tgs if sys_name == system}
        peers |= {p.peer_id for p in self._pending.values() if p.system == system}
        peers |= {peer_id for (sys_name, _tg), (peer_id, _t) in self._echo.items() if sys_name == system}
        for peer_id in peers:
            self.drop_peer(system, peer_id)

    def trim(self, now: float) -> None:
        """Expire idle stream UUIDs, stale pending transactions and old echo sources."""
        self._last_trim = now
        for stream_id in [s for s, (_u, t) in self._stream_uuids.items() if now - t > STREAM_UUID_IDLE]:
            del self._stream_uuids[stream_id]
        for uuid in [u for u, p in self._pending.items() if now - p.t > PENDING_TTL]:
            del self._pending[uuid]
        for key in [k for k, (_p, t) in self._echo.items() if now - t > ECHO_TTL]:
            del self._echo[key]

    def _maybe_trim(self, now: float) -> None:
        if now - self._last_trim >= TRIM_INTERVAL:
            self.trim(now)
