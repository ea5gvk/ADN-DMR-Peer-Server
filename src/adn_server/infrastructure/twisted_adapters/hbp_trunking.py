# ADN DMR Peer Server - HBP MASTER: DMR Tier III trunking login and DMRT
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

"""Tier III side of the HBP MASTER (Homebrew trunking v0.6, DMRGateway ``TrunkingEnabled=1``).

A ``TRUNKING: true`` MASTER speaks only the trunking dialect:

- DTCL / DTCK / DTCC / DTCPING / DTCCL have the layout and length of RPTL / RPTK /
  RPTC / RPTPING / RPTCL: they run through the classic login branches under their
  classic name, and are answered with the DTC* names (DMRGateway tells dmrtc
  "MasterLogin" only after a DTCACK, and "MasterClose" only on a DTCCL);
- classic RPT* logins are refused with MSTNAK: hotspots belong on another MASTER;
- DTCO is kept on the peer (``TRUNK_OPTIONS``) and never applied as hotspot
  OPTIONS: a site's talkgroups come from its DMRT subscriptions;
- DTCG updates the peer's position; DMRG / RPTG are ignored;
- DMRT carries no peer ID: the site is the logged-in trunking peer at that
  address. It goes to ``TrunkingUseCases.handle_dmrt``, whose answers come back
  through :meth:`HbpTrunkingMixin.send_trunking_message`;
- DMRD to the sites (``send_peers`` / ``send_peer``): a group frame goes to every
  site subscribed to its TG (an echo TG only to the site that keyed it), a unit
  frame to the site its destination is at; each one as 71 bytes with the call
  UUID of its stream, none of the hotspot OPTIONS / slot gates (a site carries
  many calls at once on "one slot");
- DMRD from a site: the call UUID is kept for its stream and the classic 55 bytes
  go on; each stream has its own RX row (``TRUNK_RX``), never the slot's
  ``STATUS``. Once routing accepts a frame, the MASTER delivers it to its other
  sites that want it (as REPEAT does for hotspots); an echo TG frame only marks
  the site the parrot answers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

from ...application.routing.helpers import track_rx_stream
from ...application.routing.trunk_routing import TRUNK_RX_IDLE, trunk_rx_row, trunk_unit_site
from ...domain import int_id
from ...domain.dmr import decode
from ...domain.dmr.const import LC_OPT
from ...domain.hbp_protocol import HBPF_DATA_SYNC, HBPF_SLT_VHEAD, HBPF_SLT_VTERM, normalize_fixed_width_ascii
from ...domain.trunking import (
    CALL_UUID_LEN,
    DMRT_MAX_LEN,
    FROM_NETWORK,
    LOCAL_ONLY,
    TRUNK_DMRD_LEN,
    derive_stream_uuid,
    is_echo_tg,
)
from ..hbp_constants import (
    DMRG,
    DMRT,
    DTCACK,
    DTCC,
    DTCCL,
    DTCG,
    DTCK,
    DTCL,
    DTCNAK,
    DTCO,
    DTCP,
    MSTCL,
    MSTNAK,
    RPTC,
    RPTCL,
    RPTG,
    RPTK,
    RPTL,
    RPTO,
    RPTP,
)
from ..options_redaction import redact_pass_in_options

logger = logging.getLogger(__name__)

TRUNK_LOGIN_ALIASES: dict[bytes, bytes] = {
    DTCL: RPTL,
    DTCK: RPTK,
    DTCC: RPTC,  # DTCC (config) and DTCCL (close)
    DTCP: RPTP,  # DTCPING
}
"""Trunking login commands (as a 4-char switch sees them) and the classic branch that runs them."""

_CLASSIC_LOGIN = frozenset({RPTL, RPTK, RPTC, RPTO, RPTP})

DMRT_MIN_LEN = 5
DTCG_LEN = 25  # DTCG + ID + "%+08.4f%+09.4f"


def login_peer_id(data: bytes) -> bytes:
    """Repeater ID of a login / keepalive datagram, classic or trunking name."""
    if data[:5] in (RPTCL, DTCCL):
        return data[5:9]
    if data[:4] in (RPTP, DTCP):
        return data[7:11]
    return data[4:8]


class HbpTrunkingMixin:
    """DTC* login names, DTCO / DTCG and DMRT for ``HBPProtocol`` on a trunking MASTER."""

    _system: str
    _CONFIG: Any
    _config: dict[str, Any]
    _peers: dict[bytes, dict[str, Any]]
    _trunking_uc: Any
    STATUS: dict[Any, Any]
    TRUNK_RX: dict[bytes, dict[str, Any]]
    transport: Any

    def trunking_peer_connected(self, peer_id: bytes) -> bool:
        """A Tier III site that completed its login on this system."""
        peer = self._peers.get(peer_id)
        return isinstance(peer, dict) and peer.get("CONNECTION") == "YES" and peer.get("TRUNKING") is True

    def send_trunking_message(self, peer_id: bytes, payload: bytes) -> bool:
        """Write one network-to-site DMRT message; False when the site is gone or it is not one."""
        if not self.trunking_peer_connected(peer_id):
            return False
        if (
            payload[:4] != DMRT
            or not DMRT_MIN_LEN <= len(payload) <= DMRT_MAX_LEN
            or payload[4] & (FROM_NETWORK | LOCAL_ONLY) != FROM_NETWORK
        ):
            logger.warning(
                "(%s) DMRT not sent to Tier III site %s: not a network message (%d bytes)",
                self._system, int_id(peer_id), len(payload),
            )
            return False
        self.transport.write(payload, self._peers[peer_id]["SOCKADDR"])
        return True

    def _trunk_send_peers(self, packet: bytes, *, exclude: bytes | None = None) -> None:
        """A DMRD frame on this trunking MASTER, to the sites that want it (never ``exclude``)."""
        for peer_id in self._trunk_dmrd_targets(packet):
            if peer_id != exclude:
                self._trunk_send_dmrd(peer_id, packet)

    def _trunk_dmrd_targets(self, packet: bytes) -> Iterable[bytes]:
        """The sites a DMRD frame goes to, never a broadcast.

        Group: the sites subscribed to the TG (an echo TG: the site that keyed it, until a
        minute after the last frame either way). Unit: the site of this system the
        destination is at (``trunk_unit_site``: registration, or a later SUB_MAP entry).
        """
        registry = self._trunking_uc.registry if self._trunking_uc is not None else None
        dst = packet[8:11]
        if packet[15] & 0x40:
            site = trunk_unit_site(registry, self._CONFIG, dst)
            return (site[1],) if site is not None and site[0] == self._system and site[1] is not None else ()
        if registry is None:
            return ()
        tg = int_id(dst)
        if is_echo_tg(tg):
            now = time.time()
            peer_id = registry.echo_peer(self._system, tg, now)
            if peer_id is None:
                return ()
            # The playback keeps its site: a long over plays back as long as it was keyed.
            registry.note_echo_source(self._system, tg, peer_id, now)
            return (peer_id,)
        return registry.subscribers(self._system, tg)

    def _trunk_send_dmrd(self, peer_id: bytes, packet: bytes) -> None:
        """One DMRD frame to a site: its peer ID, 55 bytes, then the call UUID of the stream (71)."""
        if not self.trunking_peer_connected(peer_id):
            return
        stream_id = packet[16:20]
        registry = self._trunking_uc.registry if self._trunking_uc is not None else None
        uuid = registry.uuid_for_stream(stream_id) if registry is not None else derive_stream_uuid(stream_id)
        frame = b"".join([packet[:11], peer_id, packet[15:55]]).ljust(TRUNK_DMRD_LEN - CALL_UUID_LEN, b"\x00")
        self.transport.write(frame + uuid, self._peers[peer_id]["SOCKADDR"])

    def _trunk_rx_ingress(self, peer_id: bytes, data: bytes, pkt_time: float) -> tuple[bytes, dict[str, Any]]:
        """A site's DMRD on arrival: the classic 55 bytes (the call UUID kept for the stream) and the stream's row.

        The row's LC is the voice header's, decoded before routing reads it, so each
        call is bridged with its own service options.
        """
        stream_id = data[16:20]
        if len(data) >= TRUNK_DMRD_LEN and self._trunking_uc is not None:
            self._trunking_uc.registry.note_stream_uuid(stream_id, data[55:TRUNK_DMRD_LEN], pkt_time)
        data = data[:TRUNK_DMRD_LEN - CALL_UUID_LEN]
        row = trunk_rx_row(self.TRUNK_RX, stream_id, pkt_time)
        if "PEER" not in row:
            row["PEER"] = peer_id
            row["RX_LC"] = LC_OPT + data[8:11] + data[5:8]
        if (data[15] & 0x30) >> 4 == HBPF_DATA_SYNC and data[15] & 0x0F == HBPF_SLT_VHEAD and len(data) >= 53:
            try:
                row["RX_LC"] = decode.voice_head_term(data[20:53])["LC"]
            except Exception:
                pass
        return data, row

    def _trunk_rx_accepted(
        self,
        row: dict[str, Any],
        peer_id: bytes,
        data: bytes,
        *,
        slot: int,
        call_type: str,
        unit_data: bool,
        pkt_time: float,
    ) -> None:
        """A site's frame routing accepted: the stream's row, then its delivery on this MASTER.

        The row takes what the slot takes for a hotspot; a suppressed stream (its TG
        has a QSO elsewhere) goes no further. An echo TG frame marks the site the
        parrot answers; any other frame goes to the other sites that want it
        (subscription, registration) when the system REPEATs.
        """
        stream_id, rf_src, dst_id = data[16:20], data[5:8], data[8:11]
        suppressed = bool(row.get("_suppress_uplink"))
        if not unit_data:
            if not suppressed:
                row["RX_PEER"] = peer_id
                row["RX_SEQ"] = data[4]
                row["RX_RFS"] = rf_src
                row["RX_TYPE"] = data[15] & 0x0F
                row["RX_TGID"] = dst_id
                row["RX_TIME"] = pkt_time
                if call_type in ("group", "vcsbk"):
                    track_rx_stream(
                        self.STATUS[slot], stream_id, peer_id, rf_src, dst_id, pkt_time,
                        ended=(data[15] & 0x30) >> 4 == HBPF_DATA_SYNC and data[15] & 0x0F == HBPF_SLT_VTERM,
                    )
            row["RX_STREAM_ID"] = stream_id
        if suppressed:
            return
        if call_type != "unit" and is_echo_tg(int_id(dst_id)):
            if self._trunking_uc is not None:
                self._trunking_uc.registry.note_echo_source(self._system, int_id(dst_id), peer_id, pkt_time)
            return
        if self._config.get("REPEAT", True):
            self._trunk_send_peers(data, exclude=peer_id)

    def _trunk_maintenance(self, now: float) -> None:
        """Every PING_TIME: the registry's timers, and the rows of streams idle for a minute."""
        if self._trunking_uc is not None:
            self._trunking_uc.registry.trim(now)
        for stream_id in [sid for sid, row in self.TRUNK_RX.items() if now - row.get("LAST", 0) > TRUNK_RX_IDLE]:
            del self.TRUNK_RX[stream_id]

    def _trunk_site_gone(self, peer_id: bytes) -> None:
        """A site left (timeout, DTCCL, NAK, re-login): its subscriptions and stream rows (its radios stay registered)."""
        for stream_id in [sid for sid, row in self.TRUNK_RX.items() if row.get("PEER") == peer_id]:
            del self.TRUNK_RX[stream_id]
        if self._trunking_uc is not None:
            self._trunking_uc.peer_disconnected(self._system, peer_id)

    def _close_opcode(self, peer_id: bytes) -> bytes:
        """MSTCL for a hotspot, DTCCL for a Tier III site."""
        peer = self._peers.get(peer_id)
        return DTCCL if isinstance(peer, dict) and peer.get("TRUNKING") is True else MSTCL

    def _trunk_master_datagram(self, data: bytes, sockaddr: tuple[str, int]) -> bytes | None:
        """First look at a non-DMRD datagram on a trunking MASTER.

        Returns what the classic branches must run (a DTC* login renamed to its
        RPT* twin, anything else unchanged), or None when it was handled here.
        """
        command = data[:4]
        classic = TRUNK_LOGIN_ALIASES.get(command)
        if classic is not None:
            peer_id = login_peer_id(data)
            peer = self._peers.get(peer_id)
            if command != DTCL and isinstance(peer, dict) and peer.get("TRUNKING") is not True:
                self.transport.write(b"".join([DTCNAK, peer_id]), sockaddr)
                logger.warning(
                    "(%s) %s from Radio ID %s refused: it did not log in with DTCL",
                    self._system, command.decode("ascii", errors="replace"), int_id(peer_id),
                )
                return None
            return b"".join([classic, data[4:]])
        if command in _CLASSIC_LOGIN:
            peer_id = login_peer_id(data)
            self.transport.write(b"".join([MSTNAK, peer_id]), sockaddr)
            logger.warning(
                "(%s) Classic HBP %s from %s Radio ID: %s refused: Tier III trunking system "
                "(DMRGateway TrunkingEnabled=1 only)",
                self._system, command.decode("ascii", errors="replace"), sockaddr[0], int_id(peer_id),
            )
            return None
        if command == DMRT:
            self._trunk_dmrt_received(data, sockaddr)
        elif command == DTCO:
            self._trunk_options_received(data, sockaddr)
        elif command == DTCG:
            self._trunk_position_received(data, sockaddr)
        elif command in (DMRG, RPTG):
            logger.debug("(%s) %s from %s:%s ignored (Tier III trunking system)", self._system, command, *sockaddr)
        else:
            return data
        return None

    def _trunk_site_at(self, peer_id: bytes, sockaddr: tuple[str, int]) -> dict[str, Any] | None:
        peer = self._peers.get(peer_id)
        if not isinstance(peer, dict) or peer.get("TRUNKING") is not True or peer.get("SOCKADDR") != sockaddr:
            return None
        return peer

    def _trunk_dmrt_received(self, data: bytes, sockaddr: tuple[str, int]) -> None:
        peer_id = next(
            (pid for pid, peer in self._peers.items()
             if peer.get("SOCKADDR") == sockaddr and self.trunking_peer_connected(pid)),
            None,
        )
        if peer_id is None:
            logger.debug("(%s) DMRT from %s:%s ignored: no Tier III site logged in there", self._system, *sockaddr)
            return
        if not DMRT_MIN_LEN <= len(data) <= DMRT_MAX_LEN:
            logger.debug("(%s) DMRT of %d bytes from Tier III site %s ignored", self._system, len(data), int_id(peer_id))
            return
        if self._trunking_uc is not None:
            self._trunking_uc.handle_dmrt(self._system, peer_id, data, time.time())

    def _trunk_options_received(self, data: bytes, sockaddr: tuple[str, int]) -> None:
        peer_id = data[4:8]
        peer = self._trunk_site_at(peer_id, sockaddr)
        if peer is None:
            self.transport.write(b"".join([DTCNAK, peer_id]), sockaddr)
            logger.info("(%s) Options from Radio ID that is not logged: %s", self._system, int_id(peer_id))
            return
        # Kept for the record only: a site's talkgroups come from its DMRT subscriptions.
        peer["TRUNK_OPTIONS"] = normalize_fixed_width_ascii(data[8:])
        self.transport.write(b"".join([DTCACK, peer_id]), sockaddr)
        logger.info(
            "(%s) Tier III site %s has sent options %s (not applied)",
            self._system, int_id(peer_id), redact_pass_in_options(peer["TRUNK_OPTIONS"]),
        )

    def _trunk_position_received(self, data: bytes, sockaddr: tuple[str, int]) -> None:
        peer_id = data[4:8]
        peer = self._trunk_site_at(peer_id, sockaddr)
        if len(data) < DTCG_LEN or peer is None or peer.get("CONNECTION") != "YES":
            logger.debug("(%s) DTCG from %s:%s ignored", self._system, *sockaddr)
            return
        peer["LATITUDE"] = data[8:16]
        peer["LONGITUDE"] = data[16:25]
        logger.debug("(%s) Tier III site %s position %s %s", self._system, int_id(peer_id), data[8:16], data[16:25])
