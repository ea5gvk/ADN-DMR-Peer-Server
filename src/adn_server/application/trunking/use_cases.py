# ADN DMR Peer Server - DMR Tier III trunking signalling
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

"""What the server answers to a Tier III site's DMRT message (Brandmeister "DMRTC link" semantics).

- 0x01 registration: accepted unless the subscriber ACL denies the MS; the MS is
  then at this site (and in SUB_MAP, so private calls find it). If it was at
  another site, that site gets a 0x42 so it lets the MS go. Answer: one 0x41.
- 0x02 de-registration: forgotten if it was here (and its SUB_MAP entry at this
  site with it); always answered with 0x42.
- 0x03 / 0x04 group (un)subscription: per-site talkgroups, answered with
  0x43 / 0x44 (0 for each TG refused). The first site of a system to hold a TG,
  and the last to drop it, are told to the subscription port (routing legs).
- 0x05 UDT and 0x0A status: group -> every other site subscribed to the TG, on
  any trunking system; private -> the site where the destination is registered.
  The UDT accept (0x06) goes back to the site that sent the UDT.
- 0x07 private call setup -> the site of the called MS, or a 0x49 reject back
  when it is nowhere; its accept / reject (0x08 / 0x09) go back to the caller.

Everything a site receives is the message verbatim with the from-network bit
set. Collaborators are injected (no Twisted here): ``send_message`` writes a
DMRT datagram to a connected site, ``acl_check`` is the router's.

ACLs follow the DMRD gate: each scope (GLOBAL, then the system) is applied only
when its ``USE_ACL`` is true. A TG is permitted when one timeslot's ACLs (TS1
in both scopes, or TS2 in both scopes) allow it — a site has no timeslots.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from adn_server.application.trunking.registry import (
    PENDING_PRIVATE_CALL,
    PENDING_UDT,
    Site,
    TrunkingRegistry,
)
from adn_server.domain import int_id
from adn_server.domain.trunking import (
    DmrtCallSetup,
    DmrtOp,
    DmrtRegistration,
    DmrtReply,
    DmrtStatus,
    DmrtSubscription,
    DmrtUdt,
    build_deregistration,
    build_group_confirmation,
    build_pc_reject,
    build_registration_confirmation,
    parse_dmrt,
    trunk_tg_is_valid,
    with_network_opcode,
)

logger = logging.getLogger(__name__)

MAX_TGS_PER_SITE = 512

AclCheck = Callable[[bytes | int, tuple[bool, list[tuple[int, int]]]], bool]


class TrunkSubscriptionPort(Protocol):
    """Routing side of a subscription: keeps a system's legs on the TGs its sites hold."""

    def trunk_tg_added(self, system: str, tg: int) -> None:
        """First site of ``system`` subscribed ``tg``."""
        ...

    def trunk_tg_removed(self, system: str, tg: int) -> None:
        """No site of ``system`` holds ``tg`` any more."""
        ...

    def trunk_resync(self, system: str) -> None:
        """Re-apply every TG the registry still holds for ``system`` (after a reset / reload)."""
        ...


class NoopTrunkSubscriptionPort:
    """Subscription port that does nothing (until routing provides the real one)."""

    def trunk_tg_added(self, system: str, tg: int) -> None:
        return None

    def trunk_tg_removed(self, system: str, tg: int) -> None:
        return None

    def trunk_resync(self, system: str) -> None:
        return None


class TrunkingUseCases:
    """DMRT signalling between Tier III sites and the registry."""

    def __init__(
        self,
        registry: TrunkingRegistry,
        *,
        get_config: Callable[[], dict[str, Any]],
        acl_check: AclCheck,
        send_message: Callable[[str, bytes, bytes], bool],
        is_peer_connected: Callable[[str, bytes], bool],
        subscription_port: TrunkSubscriptionPort | None = None,
        sub_map_set: Callable[[int, str, bytes, float], None] | None = None,
        sub_map_clear: Callable[[int, str, bytes], None] | None = None,
    ) -> None:
        self._registry = registry
        self._get_config = get_config
        self._acl_check = acl_check
        self._send_message = send_message
        self._is_peer_connected = is_peer_connected
        self._port: TrunkSubscriptionPort = subscription_port or NoopTrunkSubscriptionPort()
        self._sub_map_set = sub_map_set
        self._sub_map_clear = sub_map_clear
        self._drop_logged: set[tuple[str, bytes, int]] = set()

    @property
    def registry(self) -> TrunkingRegistry:
        return self._registry

    def handle_dmrt(self, system: str, peer_id: bytes, payload: bytes, now: float) -> None:
        """One DMRT datagram from a logged-in site of ``system``."""
        msg = parse_dmrt(payload)
        if msg is None:
            self._log_drop(system, peer_id, payload)
            return
        if isinstance(msg, DmrtRegistration):
            if msg.op == DmrtOp.REGISTRATION:
                self._registration(system, peer_id, msg, now)
            else:
                self._deregistration(system, peer_id, msg, now)
        elif isinstance(msg, DmrtSubscription):
            if msg.op == DmrtOp.GROUP_SUB:
                self._subscribe(system, peer_id, msg)
            else:
                self._unsubscribe(system, peer_id, msg)
        elif isinstance(msg, DmrtUdt):
            self._udt(system, peer_id, msg, payload, now)
        elif isinstance(msg, DmrtCallSetup):
            self._call_setup(system, peer_id, msg, payload, now)
        elif isinstance(msg, DmrtStatus):
            self._status(system, peer_id, msg, payload)
        elif isinstance(msg, DmrtReply):
            kind = PENDING_UDT if msg.op == DmrtOp.UDT_ACCEPT else PENDING_PRIVATE_CALL
            self._reply_to_origin(system, peer_id, msg, kind, payload, now)

    def peer_disconnected(self, system: str, peer_id: bytes) -> None:
        """A site left (DTCCL, timeout, NAK, re-login): release its talkgroups; its radios stay registered there."""
        released = self._registry.drop_peer(system, peer_id)
        for tg in released:
            self._port.trunk_tg_removed(system, tg)
        self._drop_logged = {key for key in self._drop_logged if key[:2] != (system, peer_id)}
        if released:
            logger.info(
                "(%s) Tier III site %s gone: released TGs %s", system, int_id(peer_id), released,
            )

    def system_removed(self, system: str) -> None:
        """The system was removed, or flipped in or out of trunking, on reload: release its talkgroups."""
        for tg in self._registry.system_tgs(system):
            self._port.trunk_tg_removed(system, tg)
        self._registry.drop_system(system)
        self._drop_logged = {key for key in self._drop_logged if key[0] != system}

    # --- registration --------------------------------------------------

    def _subscriber_permitted(self, system: str, ms_id: int) -> bool:
        for cfg in self._acl_scopes(system):
            if not self._acl_check(ms_id, cfg.get("SUB_ACL", (True, []))):
                return False
        return True

    def _registration(self, system: str, peer_id: bytes, msg: DmrtRegistration, now: float) -> None:
        statuses: list[bool] = []
        roamed: dict[Site, list[int]] = {}
        for ms_id in msg.ms_ids:
            accepted = ms_id != 0 and self._subscriber_permitted(system, ms_id)
            statuses.append(accepted)
            if not accepted:
                logger.info(
                    "(%s) Tier III site %s: registration of MS %s DENIED", system, int_id(peer_id), ms_id,
                )
                continue
            previous = self._registry.register(ms_id, system, peer_id, now)
            if self._sub_map_set is not None:
                self._sub_map_set(ms_id, system, peer_id, now)
            if previous is not None and self._is_peer_connected(*previous):
                roamed.setdefault(previous, []).append(ms_id)
            if previous is not None:
                logger.info(
                    "(%s) Tier III site %s: MS %s registered (roamed from %s site %s)",
                    system, int_id(peer_id), ms_id, previous[0], int_id(previous[1]),
                )
            else:
                logger.info("(%s) Tier III site %s: MS %s registered", system, int_id(peer_id), ms_id)
        ts = int(now)
        self._send(system, peer_id, build_registration_confirmation(statuses, list(msg.ms_ids), ts))
        for (old_system, old_peer), ms_ids in roamed.items():
            self._send(old_system, old_peer, build_deregistration(ms_ids, ts))

    def _deregistration(self, system: str, peer_id: bytes, msg: DmrtRegistration, now: float) -> None:
        for ms_id in msg.ms_ids:
            if self._registry.deregister(ms_id, system, peer_id):
                if self._sub_map_clear is not None:
                    self._sub_map_clear(ms_id, system, peer_id)
                logger.info("(%s) Tier III site %s: MS %s de-registered", system, int_id(peer_id), ms_id)
        self._send(system, peer_id, build_deregistration(list(msg.ms_ids), int(now)))

    # --- subscriptions -------------------------------------------------

    def _tg_permitted(self, system: str, tg: int) -> bool:
        scopes = self._acl_scopes(system)
        return any(
            all(self._acl_check(tg, cfg.get(f"TG{slot}_ACL", (True, []))) for cfg in scopes)
            for slot in (1, 2)
        )

    def _subscribe(self, system: str, peer_id: bytes, msg: DmrtSubscription) -> None:
        reply: list[int] = []
        accepted: list[int] = []
        refused: list[int] = []
        for tg in msg.tgs:
            if not self._registry.is_subscribed(system, peer_id, tg) and (
                not trunk_tg_is_valid(tg)
                or not self._tg_permitted(system, tg)
                or self._registry.peer_tg_count(system, peer_id) >= MAX_TGS_PER_SITE
            ):
                reply.append(0)
                refused.append(tg)
                continue
            if self._registry.subscribe(system, peer_id, tg):
                self._port.trunk_tg_added(system, tg)
            reply.append(tg)
            accepted.append(tg)
        self._send(system, peer_id, build_group_confirmation(DmrtOp.GROUP_SUB, reply))
        if accepted:
            logger.info("(%s) Tier III site %s subscribed TGs %s", system, int_id(peer_id), accepted)
        if refused:
            logger.info("(%s) Tier III site %s: subscription REFUSED for TGs %s", system, int_id(peer_id), refused)

    def _unsubscribe(self, system: str, peer_id: bytes, msg: DmrtSubscription) -> None:
        dropped: list[int] = []
        for tg in msg.tgs:
            if not self._registry.is_subscribed(system, peer_id, tg):
                continue
            dropped.append(tg)
            if self._registry.unsubscribe(system, peer_id, tg):
                self._port.trunk_tg_removed(system, tg)
        self._send(system, peer_id, build_group_confirmation(DmrtOp.GROUP_UNSUB, list(msg.tgs)))
        if dropped:
            logger.info("(%s) Tier III site %s unsubscribed TGs %s", system, int_id(peer_id), dropped)

    # --- UDT, status, private calls ------------------------------------

    def _targets(self, system: str, peer_id: bytes, dst: int, group: bool) -> list[Site]:
        """Connected sites a group / private message to ``dst`` goes to (never the sender)."""
        if group:
            sites = self._registry.all_subscriber_peers(dst)
        else:
            location = self._registry.location(dst)
            sites = [location] if location is not None else []
        return [site for site in sites if site != (system, peer_id) and self._is_peer_connected(*site)]

    def _forward(self, sites: list[Site], payload: bytes) -> list[Site]:
        out = with_network_opcode(payload)
        return [site for site in sites if self._send(site[0], site[1], out)]

    def _udt(self, system: str, peer_id: bytes, msg: DmrtUdt, payload: bytes, now: float) -> None:
        delivered = self._forward(self._targets(system, peer_id, msg.dst, msg.group), payload)
        kind = "group" if msg.group else "private"
        if not delivered:
            logger.info(
                "(%s) Tier III site %s: %s UDT from %s to %s has no destination site",
                system, int_id(peer_id), kind, msg.src, msg.dst,
            )
            return
        self._registry.note_pending(msg.uuid, PENDING_UDT, system, peer_id, now)
        logger.info(
            "(%s) Tier III site %s: %s UDT from %s to %s -> %s",
            system, int_id(peer_id), kind, msg.src, msg.dst, _sites_text(delivered),
        )

    def _status(self, system: str, peer_id: bytes, msg: DmrtStatus, payload: bytes) -> None:
        delivered = self._forward(self._targets(system, peer_id, msg.dst, msg.group), payload)
        logger.debug(
            "(%s) Tier III site %s: %s status %s from %s to %s -> %s",
            system, int_id(peer_id), "group" if msg.group else "private",
            msg.status, msg.src, msg.dst, _sites_text(delivered) or "nobody",
        )

    def _call_setup(self, system: str, peer_id: bytes, msg: DmrtCallSetup, payload: bytes, now: float) -> None:
        delivered = self._forward(self._targets(system, peer_id, msg.dst, group=False), payload)
        if delivered:
            self._registry.note_pending(msg.uuid, PENDING_PRIVATE_CALL, system, peer_id, now)
            logger.info(
                "(%s) Tier III site %s: private call setup %s -> %s -> %s",
                system, int_id(peer_id), msg.src, msg.dst, _sites_text(delivered),
            )
            return
        self._send(system, peer_id, build_pc_reject(msg.uuid, msg.src, msg.dst))
        logger.info(
            "(%s) Tier III site %s: private call setup %s -> %s rejected (MS not registered at another site)",
            system, int_id(peer_id), msg.src, msg.dst,
        )

    def _reply_to_origin(
        self, system: str, peer_id: bytes, msg: DmrtReply, kind: str, payload: bytes, now: float,
    ) -> None:
        origin = self._registry.pending(msg.uuid, now)
        if origin is None or origin.kind != kind:
            logger.debug(
                "(%s) Tier III site %s: DMRT 0x%02X for unknown UUID %s dropped",
                system, int_id(peer_id), int(msg.op), msg.uuid.hex(),
            )
            return
        site = (origin.system, origin.peer_id)
        if not self._is_peer_connected(*site) or not self._forward([site], payload):
            logger.debug(
                "(%s) Tier III site %s: DMRT 0x%02X origin %s site %s is gone",
                system, int_id(peer_id), int(msg.op), origin.system, int_id(origin.peer_id),
            )
            return
        logger.debug(
            "(%s) Tier III site %s: DMRT 0x%02X %s -> %s returned to %s site %s",
            system, int_id(peer_id), int(msg.op), msg.src, msg.dst, origin.system, int_id(origin.peer_id),
        )

    # --- helpers -------------------------------------------------------

    def _acl_scopes(self, system: str) -> list[dict[str, Any]]:
        """GLOBAL then the system, each only when its ``USE_ACL`` is true (as the DMRD gate)."""
        config = self._get_config()
        scopes = (config.get("GLOBAL", {}), config.get("SYSTEMS", {}).get(system, {}))
        return [cfg for cfg in scopes if cfg.get("USE_ACL")]

    def _send(self, system: str, peer_id: bytes, payload: bytes) -> bool:
        return bool(self._send_message(system, peer_id, payload))

    def _log_drop(self, system: str, peer_id: bytes, payload: bytes) -> None:
        opcode = payload[4] if isinstance(payload, (bytes, bytearray)) and len(payload) > 4 else -1
        key = (system, peer_id, opcode)
        if key in self._drop_logged:
            return
        self._drop_logged.add(key)
        logger.debug(
            "(%s) Tier III site %s: DMRT opcode %s (%d bytes) not accepted from a site, dropped",
            system, int_id(peer_id), f"0x{opcode:02X}" if opcode >= 0 else "none", len(payload),
        )


def _sites_text(sites: list[Site]) -> str:
    return ", ".join(f"{system}/{int_id(peer_id)}" for system, peer_id in sites)
