# ADN DMR Peer Server - routing: DMR Tier III (TRUNKING MASTER) side
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

"""Tier III in routing: store legs of the sites' subscriptions, and where a unit is.

``RoutingUseCases`` is the ``TrunkSubscriptionPort`` of the trunking use cases.
The first site of a trunking MASTER to subscribe a TG arms the system's TRUNK
legs (creating the table as a user-activated TG would), the last one to drop it
turns them back into INBAND IDLE legs. ``trunk_resync`` re-arms every TG the
registry still holds: after a BRIDGERESET, and when a reload re-applies the
startup subscriptions.

A private call or unit data to a radio registered at a site goes to that site
alone (:func:`trunk_unit_site`): its registration, unless SUB_MAP heard the
radio later elsewhere (dmrtc never de-registers a radio that walked off the
repeater), then SUB_MAP when it points at a trunking system. The registry is
attached at bootstrap (one per process, outside the config).

A site carries many calls at once with the same slot bit, so its uplink is not
kept in the slot's ``STATUS``: each stream has its own row in the trunking
MASTER's ``TRUNK_RX`` (:func:`trunk_rx_row`), with the keys a slot has. Routing
reads that row wherever it reads a hotspot's slot (packet control and its
new-stream checks, START / END RX, the source LC, uplink suppression).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ...domain import int_id
from ..ports import SubscriptionStore
from ..subscription.subscription_queries import store_has_table
from ..subscription.trunk_legs_ops import release_trunk_legs_store, upsert_trunk_legs_store
from ..trunking.config import system_is_trunking
from ..trunking.registry import TrunkingRegistry
from .helpers import unit_data_reportable

logger = logging.getLogger(__name__)

TrunkSite = tuple[str, "bytes | None"]

TRUNK_RX_IDLE = 60.0
"""Seconds a site stream's row outlives its last frame (a late frame still meets its own row)."""


def trunk_rx_row(rows: dict[bytes, dict[str, Any]], stream_id: bytes, now: float) -> dict[str, Any]:
    """The row of one uplink stream of a Tier III site, created on its first frame.

    What ``STATUS[slot]`` is to a hotspot, with the same keys (``RX_*``, ``RX_LC``,
    packet control, ``_suppress_uplink``), for this stream alone. ``LAST`` is its
    last frame, ``PEER`` the site (set by the MASTER).
    """
    row = rows.get(stream_id)
    if row is None:
        row = rows[stream_id] = {"START": now}
    row["LAST"] = now
    return row


def trunk_unit_site(
    registry: TrunkingRegistry | None, config: dict[str, Any], dst_id: bytes,
) -> TrunkSite | None:
    """``(system, peer_id)`` of the Tier III site unit ``dst_id`` is at, if any.

    Its registration, unless SUB_MAP heard the unit somewhere since; else a
    SUB_MAP entry on a trunking system (the peer may be unknown there: such a
    unit gets nothing, never a broadcast).
    """
    entry = (config.get("_SUB_MAP") or {}).get(dst_id)
    registration = registry.registration(int_id(dst_id)) if registry is not None else None
    if registration is not None and not (entry and entry[2] > registration.t):
        return (registration.system, registration.peer_id)
    if entry and system_is_trunking(config.get("SYSTEMS", {}).get(entry[0])):
        return (entry[0], entry[3] if len(entry) > 3 else None)
    return None


class TrunkRoutingMixin:
    """TRUNK legs (subscription port) and unit routing to Tier III sites for ``RoutingUseCases``."""

    _config: dict[str, Any]
    _subscription_store: SubscriptionStore
    _get_protocols: Any
    _trunk_registry: TrunkingRegistry | None = None

    def attach_trunking_registry(self, registry: TrunkingRegistry) -> None:
        """The process-wide Tier III registry (where units are, which TGs each site holds)."""
        self._trunk_registry = registry

    # --- TrunkSubscriptionPort ------------------------------------------

    def trunk_tg_added(self, system: str, tg: int) -> None:
        """First site of ``system`` subscribed ``tg``: the system holds it on both slots."""
        self._arm_trunk_tg(system, tg)
        self._send_routing_table_snapshot(incremental=True)

    def trunk_tg_removed(self, system: str, tg: int) -> None:
        """No site of ``system`` holds ``tg`` any more (also for a system gone on reload)."""
        sys_cfg = self._config.get("SYSTEMS", {}).get(system, {})
        timeout_sec = self._yaml_default_ua_timer(sys_cfg) * 60.0
        if release_trunk_legs_store(self._subscription_store, tg, system, timeout_sec, time.time()):
            self._send_routing_table_snapshot(incremental=True)

    def trunk_resync(self, system: str) -> None:
        """Re-arm every TG a site of ``system`` still holds (after a BRIDGERESET or a reload)."""
        registry = self._trunk_registry
        tgs = registry.system_tgs(system) if registry is not None else []
        for tg in tgs:
            self._arm_trunk_tg(system, tg)
        if tgs:
            logger.info("(%s) Tier III subscriptions re-applied: TGs %s", system, tgs)
            self._send_routing_table_snapshot(incremental=True)

    def _arm_trunk_tg(self, system: str, tg: int) -> None:
        if not store_has_table(self._subscription_store, str(tg)):
            sys_cfg = self._config.get("SYSTEMS", {}).get(system, {})
            self.ensure_dynamic_relay(tg, system, 1, self._yaml_default_ua_timer(sys_cfg))
            self.apply_static_tg_to_bridge(tg)
        upsert_trunk_legs_store(self._subscription_store, tg, system)

    # --- uplink from a site ----------------------------------------------

    def _trunk_rx_row(self, system_name: str, stream_id: bytes) -> dict[str, Any] | None:
        """The row of ``stream_id`` when ``system_name`` is a trunking MASTER, else None (use the slot)."""
        if not system_is_trunking(self._config.get("SYSTEMS", {}).get(system_name)):
            return None
        protocols = self._get_protocols() if self._get_protocols else {}
        rows = getattr(protocols.get(system_name), "TRUNK_RX", None)
        if not isinstance(rows, dict):
            return None
        return trunk_rx_row(rows, stream_id, time.time())

    # --- private calls and unit data ------------------------------------

    def _trunk_unit_site(self, dst_id: bytes) -> TrunkSite | None:
        """``(system, peer_id)`` of the Tier III site unit ``dst_id`` is at, if any (:func:`trunk_unit_site`)."""
        return trunk_unit_site(self._trunk_registry, self._config, dst_id)

    def _trunk_unit_registered(self, site: TrunkSite | None, dst_id: bytes) -> bool:
        """``dst_id`` is registered at ``site`` and the site is logged in: served there, however long ago.

        A site found through SUB_MAP alone (or one that left) ages like any SUB_MAP entry.
        """
        registry = self._trunk_registry
        return (
            site is not None
            and registry is not None
            and registry.location(int_id(dst_id)) == site
            and self._trunk_site_protocol(*site) is not None
        )

    def _trunk_site_protocol(self, system: str, peer_id: bytes | None) -> Any:
        """The protocol of ``system`` when ``peer_id`` is one of its connected sites, else None."""
        protocols = self._get_protocols() if self._get_protocols else {}
        proto = protocols.get(system)
        connected = getattr(proto, "trunking_peer_connected", None)
        if peer_id is None or not callable(connected) or not connected(peer_id):
            return None
        return proto

    def _send_data_to_trunk_site(
        self,
        source_system: str,
        site: TrunkSite,
        data: bytes,
        rf_src: bytes,
        dst_id: bytes,
        stream_id: bytes,
        peer_id: bytes,
    ) -> bool:
        """Unit data to the Tier III site ``dst_id`` is at, and only there.

        No idle / voice-busy gate: a site carries data next to its calls. Delivery
        between sites of one system is the MASTER's own (as for hotspots).
        """
        d_system, d_peer_id = site
        if d_system == source_system:
            return False
        proto = self._trunk_site_protocol(d_system, d_peer_id)
        if proto is None:
            logger.info(
                "(%s) UNIT Data to %s not sent: its Tier III site on %s is not connected",
                source_system, int_id(dst_id), d_system,
            )
            return False
        proto.send_peer(d_peer_id, data)
        logger.info(
            "(%s) UNIT Data Bridged to Tier III site %s on %s DST_ID: %s",
            source_system, int_id(d_peer_id), d_system, int_id(dst_id),
        )
        if unit_data_reportable(data[15] & 0xF if len(data) > 15 else 0):
            self._send_routing_event(
                "UNIT DATA,DATA,TX,{},{},{},{},{},{}".format(
                    d_system, int_id(stream_id), int_id(peer_id), int_id(rf_src), 1, int_id(dst_id),
                )
            )
        return True
