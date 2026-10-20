# ADN DMR Peer Server - application subscription: DMR Tier III (TRUNK) legs
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

"""Store legs of a trunking MASTER for the talkgroups its Tier III sites subscribe.

A site has no timeslots, so the system holds the TG on both slots: the uplink
source guard passes whatever slot bit DMRGateway sets, and the forward plan
collapses the two into one downlink leg. A TRUNK leg is ACTIVE, has no timer and
no in-band trigger; when the last site drops the TG it turns back into the plain
INBAND IDLE leg every MASTER has on a table (it is never deleted: a table
without the system's leg would no longer route that system's uplink).
"""

from __future__ import annotations

from adn_server.application.ports import SubscriptionStore
from adn_server.domain import bytes_3
from adn_server.domain.subscription import (
    ActivationPolicy,
    AudioChannel,
    InbandTriggers,
    Subscription,
    SubscriptionId,
    SubscriptionPhase,
    SubscriptionRole,
    SubscriptionState,
    SystemId,
    TgId,
)

TRUNK_SLOTS = (1, 2)


def _leg_id(system: str, tg: int, slot: int) -> SubscriptionId:
    return SubscriptionId(channel=AudioChannel(tgid=TgId(tg), slot=slot), system=SystemId(system))  # type: ignore[arg-type]


def upsert_trunk_legs_store(store: SubscriptionStore, tg: int, system: str) -> None:
    """``system`` holds ``tg`` for its sites: ACTIVE TRUNK legs on both slots (table must exist)."""
    for slot in TRUNK_SLOTS:
        store.upsert(
            Subscription(
                channel=AudioChannel(tgid=TgId(tg), slot=slot),  # type: ignore[arg-type]
                system=SystemId(system),
                target_tgid=TgId(tg),
                role=SubscriptionRole.SINK,
                policy=ActivationPolicy.TRUNK,
                state=SubscriptionState(phase=SubscriptionPhase.ACTIVE, timer_expires_at=None),
                timeout_seconds=None,
                triggers=InbandTriggers(),
            )
        )


def release_trunk_legs_store(
    store: SubscriptionStore,
    tg: int,
    system: str,
    timeout_sec: float,
    now: float,
) -> bool:
    """No site of ``system`` holds ``tg`` any more: its TRUNK legs become INBAND IDLE legs.

    Legs that are no longer TRUNK (a BRIDGERESET already turned them) are left as
    they are. Returns whether a leg changed.
    """
    changed = False
    for slot in TRUNK_SLOTS:
        leg = store.get(_leg_id(system, tg, slot))
        if leg is None or leg.policy != ActivationPolicy.TRUNK:
            continue
        leg.policy = ActivationPolicy.INBAND
        leg.state = SubscriptionState(phase=SubscriptionPhase.IDLE, timer_expires_at=now + timeout_sec)
        leg.timeout_seconds = timeout_sec
        leg.triggers = InbandTriggers(on=(bytes_3(tg),), off=(), reset=())
        store.upsert(leg)
        changed = True
    return changed
