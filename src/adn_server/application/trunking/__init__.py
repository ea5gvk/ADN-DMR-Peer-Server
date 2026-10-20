# ADN DMR Peer Server - application trunking init
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

"""DMR Tier III (Homebrew trunking protocol v0.6): config flag, registry and DMRT use cases."""

from .config import system_is_trunking
from .registry import TrunkingRegistry
from .use_cases import NoopTrunkSubscriptionPort, TrunkingUseCases, TrunkSubscriptionPort

__all__ = [
    "NoopTrunkSubscriptionPort",
    "TrunkSubscriptionPort",
    "TrunkingRegistry",
    "TrunkingUseCases",
    "system_is_trunking",
]
