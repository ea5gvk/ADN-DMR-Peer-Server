# ADN DMR Peer Server - DMR Tier III trunking: which systems trunk
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

"""``system_is_trunking`` is the one place that decides whether a system speaks Tier III."""

from __future__ import annotations

from typing import Any


def system_is_trunking(sys_cfg: dict[str, Any] | None) -> bool:
    """True for a MASTER with ``TRUNKING: true`` (DMRGateway ``TrunkingEnabled=1`` sites)."""
    return bool(sys_cfg) and sys_cfg.get("MODE") == "MASTER" and sys_cfg.get("TRUNKING") is True
