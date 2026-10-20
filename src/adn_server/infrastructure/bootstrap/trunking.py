# ADN DMR Peer Server - DMR Tier III trunking wiring
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

"""One Tier III registry and its DMRT use cases per process, reaching the sites through the HBP protocols."""

from __future__ import annotations

from typing import Any

from adn_server.application.routing.sub_map_learning import system_learns_sub_map
from adn_server.application.trunking import (
    NoopTrunkSubscriptionPort,
    TrunkingRegistry,
    TrunkingUseCases,
    TrunkSubscriptionPort,
)
from adn_server.application.trunking.use_cases import AclCheck
from adn_server.domain import bytes_3


def create_trunking_use_cases(
    config: Any,
    protocols: dict[str, Any],
    acl_check: AclCheck,
    *,
    subscription_port: TrunkSubscriptionPort | None = None,
) -> TrunkingUseCases:
    """Build the process-wide trunking use cases.

    Created once at startup and kept out of the config dict, so the registry
    survives SIGHUP like the subscription store. ``config`` is the live config
    (``ConfigProxy``); ``protocols`` the live HBP protocols by system name.
    """

    def send_message(system: str, peer_id: bytes, payload: bytes) -> bool:
        send = getattr(protocols.get(system), "send_trunking_message", None)
        return bool(send(peer_id, payload)) if callable(send) else False

    def is_peer_connected(system: str, peer_id: bytes) -> bool:
        connected = getattr(protocols.get(system), "trunking_peer_connected", None)
        return bool(connected(peer_id)) if callable(connected) else False

    def sub_map_set(ms_id: int, system: str, peer_id: bytes, t: float) -> None:
        # Where private calls and unit data find a registered MS (slot 1: a site has no timeslots).
        sub_map = config.get("_SUB_MAP")
        if sub_map is not None and system_learns_sub_map(config.get("SYSTEMS", {}).get(system)):
            sub_map[bytes_3(ms_id)] = (system, 1, t, peer_id)

    def sub_map_clear(ms_id: int, system: str, peer_id: bytes) -> None:
        # The MS de-registered: it is no longer at that site (an entry elsewhere is newer, kept).
        sub_map = config.get("_SUB_MAP") or {}
        entry = sub_map.get(bytes_3(ms_id))
        if entry and entry[0] == system and len(entry) > 3 and entry[3] == peer_id:
            del sub_map[bytes_3(ms_id)]

    return TrunkingUseCases(
        TrunkingRegistry(),
        get_config=lambda: config,
        acl_check=acl_check,
        send_message=send_message,
        is_peer_connected=is_peer_connected,
        subscription_port=subscription_port or NoopTrunkSubscriptionPort(),
        sub_map_set=sub_map_set,
        sub_map_clear=sub_map_clear,
    )
