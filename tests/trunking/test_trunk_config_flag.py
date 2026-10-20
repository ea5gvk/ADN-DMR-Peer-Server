"""``system_is_trunking``: only a MASTER with ``TRUNKING: true`` speaks Tier III."""

from __future__ import annotations

import pytest

from adn_server.application.trunking import system_is_trunking


@pytest.mark.unit
@pytest.mark.parametrize(("sys_cfg", "expected"), [
    ({"MODE": "MASTER", "TRUNKING": True}, True),
    ({"MODE": "MASTER", "TRUNKING": False}, False),
    ({"MODE": "MASTER"}, False),                                         # existing configs
    ({"MODE": "MASTER", "TRUNKING": "true"}, False),                     # only a YAML bool counts
    ({"MODE": "MASTER", "TRUNKING": 1}, False),
    ({"MODE": "PEER", "TRUNKING": True}, False),
    ({"MODE": "OPENBRIDGE", "TRUNKING": True}, False),
    ({}, False),
    (None, False),
])
def test_system_is_trunking(sys_cfg, expected: bool) -> None:
    assert system_is_trunking(sys_cfg) is expected
