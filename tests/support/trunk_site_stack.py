# ADN DMR Peer Server - tests support: DMR Tier III sites on real HBP MASTERs
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

"""Real ``HBPProtocol`` MASTERs wired like bootstrap, driven the way DMRGateway (TrunkingEnabled=1) does.

Every system gets a recording transport; the trunking use cases come from
``create_trunking_use_cases`` (the bootstrap wiring) over the same protocols
registry, so a DMRT answer travels the real path: use case -> registry ->
``HBPProtocol.send_trunking_message`` -> ``transport.write``.

``TrunkRoutingStack`` adds routing as bootstrap wires it: ``RoutingUseCases``
over a real subscription store is the trunking subscription port, hotspots log
in classically on the proxy MASTER, an OpenBridge injects through routing.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

from bitarray import bitarray

from adn_server.application.routing_use_cases import RoutingUseCases
from adn_server.domain import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, bytes_4
from adn_server.domain.dmr import decode
from adn_server.domain.dmr.bptc import decode_emblc, encode_emblc, encode_header_lc
from adn_server.infrastructure.acl_router import InMemoryAclRouter
from adn_server.infrastructure.bootstrap.trunking import create_trunking_use_cases
from adn_server.infrastructure.config_loader import process_acls
from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
from adn_server.infrastructure.hbp_constants import DTCACK, DTCC, DTCK, DTCL, DTCO, RPTACK, RPTC, RPTK, RPTL, RPTO
from adn_server.infrastructure.subscription_store import InMemorySubscriptionStore
from adn_server.infrastructure.talker_alias_emblc import default_ta_emblc_encoder
from adn_server.infrastructure.twisted_adapters.udp_hbp import HBPProtocol
from tests.harness.deterministic import DeterministicScenario, FakeObpProtocol, PacketSpec, add_openbridge_system
from tests.support.hbp_repeat_stack import RecordingTransport

PASS = b"trunk-pass"

T1, T2, T3 = (bytes_4(n) for n in (2149001, 2149002, 2149003))
T1_ADDR, T2_ADDR, T3_ADDR = ("10.0.1.1", 40001), ("10.0.1.2", 40002), ("10.0.1.3", 40003)


def dtcc(peer_id: bytes, callsign: bytes = b"EA5GVK") -> bytes:
    """DTCC as DMRGateway sends it: 8 + 294 bytes (the RPTC layout)."""
    body = callsign.ljust(8) + b"438800000" + b"431200000" + b"01" + b"01" + b"+40.4168" + b"-003.7038"
    body += b"001" + b"Madrid".ljust(20) + b"Tier III site".ljust(19) + b"4" + b"".ljust(124)
    body += b"DMRGateway".ljust(40) + b"dmrtc".ljust(40)
    assert len(body) == 294
    return DTCC + peer_id + body


class TrunkStack:
    """Trunking (and optionally classic) MASTERs on one config, one router, one trunking registry."""

    def __init__(
        self,
        trunking: tuple[str, ...] = ("TRUNK",),
        classic: tuple[str, ...] = (),
        *,
        system: dict[str, Any] | None = None,
        dmrd_received: Any = None,
        dynamic_tg_uc: Any = None,
        get_user_password: Any = None,
    ) -> None:
        systems: dict[str, Any] = {}
        for name in trunking + classic:
            systems[name] = {"MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 8, "PASSPHRASE": PASS, **(system or {})}
            if name in trunking:
                systems[name].update({"TRUNKING": True, "PORT": 62037})
        self.config: dict[str, Any] = {
            "GLOBAL": {"PING_TIME": 10, "MAX_MISSED": 3, "USE_ACL": False},
            "SYSTEMS": systems,
            "_SUB_MAP": {},
        }
        process_acls(self.config)
        ensure_system_runtime_config(self.config)
        self.router = InMemoryAclRouter()
        self.protocols: dict[str, HBPProtocol] = {}
        self.transports: dict[str, RecordingTransport] = {}
        self.uc = create_trunking_use_cases(self.config, self.protocols, self.router.acl_check)
        for name in systems:
            proto = HBPProtocol(
                name, self.config, router=self.router, dmrd_received=dmrd_received,
                get_user_password_callback=get_user_password,
                dynamic_tg_uc=dynamic_tg_uc, trunking_uc=self.uc,
            )
            proto.transport = self.transports[name] = RecordingTransport()
            self.protocols[name] = proto

    @property
    def registry(self):
        return self.uc.registry

    def send(self, system: str, data: bytes, addr: tuple[str, int]) -> None:
        self.protocols[system].datagramReceived(data, addr)

    def login(self, system: str, peer_id: bytes, addr: tuple[str, int], *, options: bytes | None = None) -> None:
        """DTCL -> DTCK -> DTCC (-> DTCO), checking each DTCACK; clears the transport afterwards."""
        t = self.transports[system]
        self.send(system, DTCL + peer_id, addr)
        salt = t.sent[-1][0][6:10]
        self.send(system, DTCK + peer_id + hashlib.sha256(salt + PASS).digest(), addr)
        assert t.sent[-1] == (DTCACK + peer_id, addr)
        self.send(system, dtcc(peer_id), addr)
        assert t.sent[-1] == (DTCACK + peer_id, addr)
        if options is not None:
            self.send(system, DTCO + peer_id + options, addr)
            assert t.sent[-1] == (DTCACK + peer_id, addr)
        assert self.protocols[system]._peers[peer_id]["CONNECTION"] == "YES"
        t.clear()

    def to(self, system: str, addr: tuple[str, int]) -> list[bytes]:
        return self.transports[system].for_addr(addr)


OBP_SERVER = bytes_4(31415)  # another server on the mesh


def voice_frames(spec: PacketSpec, bursts: int = 4, *, lc: bytes | None = None) -> list[bytes]:
    """One over as a hotspot sends it: header, voice bursts B..E (embedded LC), terminator.

    ``lc`` (9 bytes) is carried in the voice header, as a radio sends its own.
    """
    head = DeterministicScenario.voice_head_spec(spec)
    if lc is not None:
        bits = bitarray(endian="big")
        bits.frombytes(head.payload)
        h_lc = encode_header_lc(lc)
        head = replace(head, payload=(h_lc[0:98] + bits[98:166] + h_lc[98:196]).tobytes())
    return (
        [head.data()]
        + [DeterministicScenario.voice_burst_spec(spec, seq=n, dtype_vseq=1 + (n - 1) % 4).data() for n in range(1, bursts + 1)]
        + [DeterministicScenario.voice_term_spec(spec, seq=bursts + 1).data()]
    )


def header_lc(frame: bytes) -> bytes:
    """Full LC of a voice header frame."""
    assert frame[15] & 0x3F == (HBPF_DATA_SYNC << 4) | HBPF_SLT_VHEAD
    return decode.voice_head_term(frame[20:53])["LC"]


def embedded_lc(frames: list[bytes]) -> bytes:
    """LC carried in the embedded signalling of voice bursts B..E."""
    by_vseq = {f[15] & 0x0F: f for f in frames if (f[15] & 0x30) >> 4 == 0}
    bits = bitarray(endian="big")
    for vseq in (1, 2, 3, 4):
        bits += decode.voice(by_vseq[vseq][20:53])["EMBED"]
    return decode_emblc(bits)


def by_stream(frames: list[bytes]) -> dict[bytes, list[bytes]]:
    out: dict[bytes, list[bytes]] = {}
    for f in frames:
        out.setdefault(f[16:20], []).append(f)
    return out


class TrunkRoutingStack(TrunkStack):
    """Trunking and classic MASTERs, OpenBridges and routing, as ``run_peer_server`` wires them.

    ``proxy_target`` is the inject-only hotspot MASTER (``PROXY.TARGET_SYSTEM``);
    OpenBridges are fake protocols fed through ``inject_obp`` (routing's OBP ingress).
    """

    def __init__(
        self,
        trunking: tuple[str, ...] = ("TRUNK",),
        classic: tuple[str, ...] = ("HOTSPOTS",),
        *,
        obp: tuple[str, ...] = ("OBP",),
        proxy_target: str | None = "HOTSPOTS",
        system: dict[str, Any] | None = None,
    ) -> None:
        systems: dict[str, Any] = {}
        for name in trunking + classic:
            systems[name] = {
                "MODE": "MASTER", "ENABLED": True, "MAX_PEERS": 8, "PASSPHRASE": PASS, "REPEAT": True,
                "SINGLE_MODE": False, "GROUP_HANGTIME": 5, "DEFAULT_UA_TIMER": 10, **(system or {}),
            }
            if name in trunking:
                systems[name].update({"TRUNKING": True, "PORT": 62037})
        self.config: dict[str, Any] = {
            "GLOBAL": {"PING_TIME": 10, "MAX_MISSED": 3, "USE_ACL": False, "SERVER_ID": bytes_4(9990)},
            "REPORTS": {"REPORT": False},
            "SYSTEMS": systems,
            "_SUB_MAP": {},
        }
        if proxy_target:
            self.config["PROXY"] = {"TARGET_SYSTEM": proxy_target}
        process_acls(self.config)
        for name in obp:
            add_openbridge_system(self.config, name)
        ensure_system_runtime_config(self.config)
        self.router = InMemoryAclRouter()
        self.store = InMemorySubscriptionStore()
        self.protocols: dict[str, Any] = {}
        self.transports: dict[str, RecordingTransport] = {}
        self.routing = RoutingUseCases(
            self.router, self.config, self.store,
            send_to_system=self._send_to_system,
            get_protocols=lambda: self.protocols,
            encode_emblc=encode_emblc,
            ta_emblc_encoder=default_ta_emblc_encoder,
        )
        self.uc = create_trunking_use_cases(
            self.config, self.protocols, self.router.acl_check, subscription_port=self.routing,
        )
        self.routing.attach_trunking_registry(self.uc.registry)
        for name in trunking + classic:
            proto = HBPProtocol(
                name, self.config, router=self.router,
                dmrd_received=self.routing.dmrd_received,
                on_in_band_signalling=self.routing.apply_in_band_signalling,
                on_options_received=self.routing.options_config_for_system,
                get_subscription_store=lambda: self.store,
                trunking_uc=self.uc,
            )
            proto.transport = self.transports[name] = RecordingTransport()
            self.protocols[name] = proto
        for name in obp:
            self.protocols[name] = FakeObpProtocol(name)
        self.routing.apply_startup_subscriptions()

    def _send_to_system(self, system: str, packet: bytes, **kwargs: Any) -> None:
        send = getattr(self.protocols.get(system), "send_system", None)
        if send is not None:
            send(packet, **kwargs)

    def login_hotspot(
        self, system: str, peer_id: bytes, addr: tuple[str, int], *, options: bytes | None = None,
    ) -> None:
        """Classic RPTL -> RPTK -> RPTC (-> RPTO) of a hotspot; clears the transport afterwards."""
        t = self.transports[system]
        self.send(system, RPTL + peer_id, addr)
        assert t.sent[-1][0][:6] == RPTACK
        salt = t.sent[-1][0][6:10]
        self.send(system, RPTK + peer_id + hashlib.sha256(salt + PASS).digest(), addr)
        self.send(system, RPTC + peer_id + b"EA5GVK  ".ljust(294, b"\x00"), addr)
        if options is not None:
            self.send(system, RPTO + peer_id + options, addr)
        assert self.protocols[system]._peers[peer_id]["CONNECTION"] == "YES"
        t.clear()

    def inject_obp(self, system: str, packet: bytes) -> Any:
        """One OpenBridge frame into routing, as the OBP adapter hands it over."""
        bits = packet[15]
        return self.routing.dmrd_received(
            system, packet[11:15], packet[5:8], packet[8:11], packet[4], 1,
            "unit" if bits & 0x40 else "group", (bits & 0x30) >> 4, bits & 0x0F, packet[16:20], packet[:53],
            obp_use_parsed=True, obp_hops=b"\x01", obp_source_server=OBP_SERVER,
        )

    def dmrd_to(self, system: str, addr: tuple[str, int]) -> list[bytes]:
        return [p for p in self.to(system, addr) if p[:4] == b"DMRD"]
