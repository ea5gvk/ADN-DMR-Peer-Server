# ADN DMR Peer Server - tests DMR Tier III TRUNKING config validation
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

"""``SYSTEMS.<name>.TRUNKING``: a MASTER of Tier III sites on its own port, nothing slot-based.

Also: the commented ``TRUNK`` block of ``adn-server.example.yaml`` is a valid
system once uncommented, and leaving it commented changes nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from adn_server.application.trunking import system_is_trunking
from adn_server.domain.errors import ConfigError
from adn_server.infrastructure.config_loader import YamlConfigLoader
from adn_server.infrastructure.config_reload import prepare_incoming_config
from adn_server.infrastructure.config_validator import validate_config
from tests.conftest import minimal_valid_config

EXAMPLE = Path(__file__).resolve().parents[2] / "adn-server.example.yaml"


def _trunk(**overrides: Any) -> dict[str, Any]:
    return {"MODE": "MASTER", "ENABLED": True, "TRUNKING": True, "PORT": 62037, "MAX_PEERS": 10, **overrides}


def _config(trunk: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    config = minimal_valid_config(**overrides)
    config["SYSTEMS"]["TRUNK"] = trunk
    return config


def _errors(config: dict[str, Any]) -> str:
    with pytest.raises(ConfigError) as exc:
        validate_config(config)
    return str(exc.value)


def test_accepts_a_trunking_master_on_its_own_port() -> None:
    validate_config(_config(_trunk(SINGLE_MODE=False, TS1_STATIC="", TS2_STATIC="", DEFAULT_REFLECTOR=0, GENERATOR=1)))


def test_accepts_trunking_false_anywhere() -> None:
    config = minimal_valid_config()
    config["SYSTEMS"]["HOTSPOT"]["TRUNKING"] = False
    validate_config(config)


def test_rejects_non_bool_trunking() -> None:
    assert "SYSTEMS.TRUNK.TRUNKING: expected boolean" in _errors(_config(_trunk(TRUNKING="yes")))


@pytest.mark.parametrize("mode", ["PEER", "OPENBRIDGE"])
def test_rejects_trunking_outside_a_master(mode: str) -> None:
    assert "SYSTEMS.TRUNK.TRUNKING: only on MASTER" in _errors(_config(_trunk(MODE=mode)))


def test_rejects_trunking_on_the_proxy_target() -> None:
    config = minimal_valid_config()
    config["SYSTEMS"]["HOTSPOT"]["TRUNKING"] = True
    msg = _errors(config)
    assert "SYSTEMS.HOTSPOT.TRUNKING: not supported on PROXY.TARGET_SYSTEM" in msg
    assert "SYSTEMS.HOTSPOT.PORT: required" not in msg


@pytest.mark.parametrize("port", [None, 0, -1, "62037", True])
def test_rejects_a_trunking_master_without_its_own_port(port: Any) -> None:
    trunk = _trunk()
    if port is None:
        del trunk["PORT"]
    else:
        trunk["PORT"] = port
    assert "SYSTEMS.TRUNK.PORT: required with TRUNKING" in _errors(_config(trunk))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"GENERATOR": 2}, "SYSTEMS.TRUNK.GENERATOR: must be 0 or 1 with TRUNKING"),
        ({"SINGLE_MODE": True}, "SYSTEMS.TRUNK.SINGLE_MODE: not supported with TRUNKING"),
        ({"TS1_STATIC": "214"}, "SYSTEMS.TRUNK.TS1_STATIC: not supported with TRUNKING"),
        ({"TS2_STATIC": "91,92"}, "SYSTEMS.TRUNK.TS2_STATIC: not supported with TRUNKING"),
        ({"DEFAULT_REFLECTOR": 9}, "SYSTEMS.TRUNK.DEFAULT_REFLECTOR: not supported with TRUNKING"),
    ],
)
def test_rejects_slot_based_settings(overrides: dict[str, Any], expected: str) -> None:
    assert expected in _errors(_config(_trunk(**overrides)))


def test_rejects_the_proxy_listen_port() -> None:
    msg = _errors(_config(_trunk(PORT=62031)))
    assert "SYSTEMS.TRUNK.PORT: 62031 is also PROXY.LISTEN_PORT" in msg


def test_rejects_the_obp_proxy_listen_port() -> None:
    config = _config(_trunk(PORT=62044), OBP_PROXY={"ENABLED": True, "LISTEN_PORT": 62044})
    assert "SYSTEMS.TRUNK.PORT: 62044 is also OBP_PROXY.LISTEN_PORT" in _errors(config)


def test_a_disabled_trunking_system_is_not_checked_for_port_collisions() -> None:
    validate_config(_config(_trunk(PORT=62031, ENABLED=False)))


def test_the_same_settings_without_trunking_stay_valid() -> None:
    """Only TRUNKING systems get the new rules: nothing that validated before is refused now."""
    validate_config(_config(_trunk(TRUNKING=False, PORT=62031, SINGLE_MODE=True, TS2_STATIC="91", DEFAULT_REFLECTOR=9)))


def _example_with_trunk_uncommented() -> str:
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    start = lines.index("  # TRUNK:")
    end = start
    while end < len(lines) and lines[end].startswith("  #"):
        end += 1
    block = [line.replace("  # ", "  ", 1) for line in lines[start:end]]
    return "\n".join(lines[:start] + block + lines[end:]) + "\n"


def test_example_trunk_block_is_a_valid_disabled_trunking_master(tmp_path) -> None:
    path = tmp_path / "adn-server.yaml"
    path.write_text(_example_with_trunk_uncommented(), encoding="utf-8")
    config = prepare_incoming_config(YamlConfigLoader(), str(path), logging.getLogger("test"))
    trunk = config["SYSTEMS"]["TRUNK"]
    assert system_is_trunking(trunk) and trunk["ENABLED"] is False and trunk["PORT"] == 62037

    enabled = yaml.safe_load(path.read_text(encoding="utf-8"))
    enabled["SYSTEMS"]["TRUNK"]["ENABLED"] = True
    path.write_text(yaml.safe_dump(enabled), encoding="utf-8")
    config = prepare_incoming_config(YamlConfigLoader(), str(path), logging.getLogger("test"))
    assert system_is_trunking(config["SYSTEMS"]["TRUNK"]) and config["SYSTEMS"]["TRUNK"]["ENABLED"] is True


def test_example_ships_without_a_trunking_system() -> None:
    systems = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))["SYSTEMS"]
    assert "TRUNK" not in systems and not any(cfg.get("TRUNKING") for cfg in systems.values())
