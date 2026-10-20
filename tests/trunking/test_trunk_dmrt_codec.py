"""DMRT codec (domain/trunking.py) against the byte layouts dmrtc builds and parses."""

from __future__ import annotations

import pytest

from adn_server.domain import bytes_3
from adn_server.domain.trunking import (
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
    DTCPING,
    DTCPONG,
    DTCSBKN,
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
    derive_stream_uuid,
    is_echo_tg,
    parse_dmrt,
    trunk_tg_is_valid,
    with_network_opcode,
)
from tests.support import dmrtc_wire as dmrtc

MS, MS2, TS = 2140501, 2140502, 1_700_000_000                 # MS = 0x20A955
UUID = bytes(range(0xA0, 0xB0))


# --- parsing what dmrtc sends ---------------------------------------------

@pytest.mark.unit
def test_parse_registration_as_dmrtc_builds_it() -> None:
    raw = dmrtc.create_registration_message(MS, TS)
    assert raw == bytes.fromhex("444d52540100f15365000000000120a955")
    assert parse_dmrt(raw) == DmrtRegistration(DmrtOp.REGISTRATION, TS, (MS,))


@pytest.mark.unit
def test_parse_deregistration_as_dmrtc_builds_it() -> None:
    msg = parse_dmrt(dmrtc.create_deregistration_message(MS, TS))
    assert msg == DmrtRegistration(DmrtOp.DEREGISTRATION, TS, (MS,))


@pytest.mark.unit
def test_parse_registration_with_several_addresses() -> None:
    raw = b"DMRT\x01" + TS.to_bytes(8, "little") + bytes((3,)) + bytes_3(MS) + bytes_3(MS2) + bytes_3(1)
    assert parse_dmrt(raw).ms_ids == (MS, MS2, 1)


@pytest.mark.unit
def test_parse_registration_timestamp_is_signed_little_endian() -> None:
    raw = b"DMRT\x01" + (-5).to_bytes(8, "little", signed=True) + b"\x01" + bytes_3(MS)
    assert parse_dmrt(raw).timestamp == -5


@pytest.mark.unit
def test_parse_group_subscription_has_no_timestamp() -> None:
    raw = dmrtc.create_group_subscription_message([91, 214])
    assert raw == bytes.fromhex("444d5254030200005b0000d6")
    assert parse_dmrt(raw) == DmrtSubscription(DmrtOp.GROUP_SUB, (91, 214))


@pytest.mark.unit
def test_parse_group_unsubscription() -> None:
    assert parse_dmrt(dmrtc.create_group_unsubscription_message([0xFFFFFF])) == DmrtSubscription(
        DmrtOp.GROUP_UNSUB, (0xFFFFFF,),
    )


@pytest.mark.unit
def test_parse_subscription_of_64_talkgroups_dmrtc_maximum() -> None:
    tgs = list(range(1000, 1070))                                   # dmrtc keeps the first 64
    raw = dmrtc.create_group_subscription_message(tgs)
    assert len(raw) == 6 + 3 * 64
    assert parse_dmrt(raw).tgs == tuple(tgs[:64])


@pytest.mark.unit
@pytest.mark.parametrize(("group", "fmt", "type_byte"), [(True, 7, 0x87), (False, 3, 0x43)])
def test_parse_udt_transfer(group: bool, fmt: int, type_byte: int) -> None:
    raw = dmrtc.create_udt_transfer_message(MS, 91 if group else MS2, b"hello", fmt, group, UUID)
    assert len(raw) == 34
    msg = parse_dmrt(raw)
    assert isinstance(msg, DmrtUdt)
    assert (msg.uuid, msg.udt_type, msg.group, msg.src, msg.dst, msg.payload) == (
        UUID, type_byte, group, MS, 91 if group else MS2, b"hello",
    )


@pytest.mark.unit
@pytest.mark.parametrize("payload", [b"", b"x" * 46])
def test_parse_udt_payload_size_limits(payload: bytes) -> None:
    msg = parse_dmrt(dmrtc.create_udt_transfer_message(MS, MS2, payload, 4, False, UUID))
    assert msg is not None and msg.payload == payload


@pytest.mark.unit
def test_parse_udt_accept() -> None:
    msg = parse_dmrt(dmrtc.create_udt_accept_message(MS2, MS, UUID))
    assert msg == DmrtReply(DmrtOp.UDT_ACCEPT, UUID, MS2, MS)


@pytest.mark.unit
def test_parse_private_call_setup() -> None:
    msg = parse_dmrt(dmrtc.create_private_call_setup_message(MS, MS2, 0x20, UUID))
    assert msg == DmrtCallSetup(DmrtOp.PC_SETUP, UUID, 0x20, MS, MS2)


@pytest.mark.unit
@pytest.mark.parametrize(("accept", "op"), [(True, DmrtOp.PC_ACCEPT), (False, DmrtOp.PC_REJECT)])
def test_parse_private_call_reply(accept: bool, op: DmrtOp) -> None:
    msg = parse_dmrt(dmrtc.create_private_call_reply_message(MS2, MS, UUID, accept))
    assert msg == DmrtReply(op, UUID, MS2, MS)


@pytest.mark.unit
@pytest.mark.parametrize("group", [True, False])
def test_parse_status_transfer(group: bool) -> None:
    msg = parse_dmrt(dmrtc.create_status_transfer_message(MS, 91, 0x15, group, UUID))
    assert isinstance(msg, DmrtStatus)
    assert (msg.group, msg.status, msg.src, msg.dst, msg.uuid) == (group, 0x15, MS, 91, UUID)


@pytest.mark.unit
def test_parse_accepts_bytearray() -> None:
    assert parse_dmrt(bytearray(dmrtc.create_registration_message(MS, TS))) is not None


@pytest.mark.unit
@pytest.mark.parametrize("raw", [
    b"", b"DMRT", b"DMRX\x01" + b"\x00" * 12,                       # too short / bad signature
    b"DMRT\x01" + b"\x00" * 5,                                      # truncated registration
    b"DMRT\x01" + b"\x00" * 8 + b"\x00" + bytes_3(MS),              # count 0
    b"DMRT\x01" + b"\x00" * 8 + b"\x02" + bytes_3(MS),              # count says 2, only 1 address
    b"DMRT\x01" + b"\x00" * 8 + b"\x09" + bytes_3(MS) * 9,          # 9 addresses > 8
    b"DMRT\x01" + b"\x00" * 8 + b"\x01" + bytes_3(MS) + b"\x00",    # trailing byte
    b"DMRT\x03\x02\x00\x00\x5b",                                    # count says 2, only 1 TG
    b"DMRT\x03\x00",                                                # no TG at all
    b"DMRT\x03\x00" + bytes_3(91),                                  # count 0
    b"DMRT\x03\x41" + bytes_3(91) * 65,                             # 65 TGs > 64
    b"DMRT\x05" + UUID + b"\x80\x2f" + b"\x00" * 6 + b"x" * 47,     # payload size 47 > 46
    b"DMRT\x05" + UUID + b"\x80\x05" + b"\x00" * 6 + b"abc",        # payload shorter than its size
    b"DMRT\x05" + UUID + b"\x80\x02" + b"\x00" * 6 + b"abc",        # payload longer than its size
    b"DMRT\x06" + b"\x00" * 21,                                     # UDT accept 26 bytes
    b"DMRT\x07" + b"\x00" * 22,                                     # call setup 27 bytes
    b"DMRT\x09" + b"\x00" * 23,                                     # call reject 28 bytes
    b"DMRT\x0a" + b"\x00" * 22,                                     # status 27 bytes
    b"DMRT\x41" + b"\x00" * 13, b"DMRT\x45" + b"\x00" * 24,         # network opcodes
    b"DMRT\xc1", b"DMRT\x81", b"DMRT\x00", b"DMRT\x0b" + b"\x00" * 30,   # local / unknown
    b"DMRT\x03" + b"\x00" * 251,                                    # 256 bytes > 255
    "DMRT\x01", None,                                               # not bytes
])
def test_malformed_is_rejected(raw) -> None:
    assert parse_dmrt(raw) is None


# --- building what dmrtc parses -------------------------------------------

@pytest.mark.unit
def test_registration_confirmation_accept_matches_dmrtc_parser() -> None:
    ok = build_registration_confirmation([True], [MS], TS)
    assert ok == bytes.fromhex("444d52544100f1536500000000010120a955") and len(ok) == 18
    assert dmrtc.validate_net_message(ok)
    assert dmrtc.parse_registration_confirmation_message(ok) == (MS, True)


@pytest.mark.unit
def test_registration_confirmation_deny_matches_dmrtc_parser() -> None:
    denied = build_registration_confirmation([False], [MS], TS)
    assert dmrtc.parse_registration_confirmation_message(denied) == (MS, False)


@pytest.mark.unit
def test_registration_confirmation_status_bitfield_is_lsb_first() -> None:
    raw = build_registration_confirmation([True, False, True], [MS, MS2, 1], TS)
    assert raw[13] == 0b101 and raw[14] == 3 and len(raw) == 15 + 9
    assert raw[15:] == bytes_3(MS) + bytes_3(MS2) + bytes_3(1)
    assert dmrtc.parse_registration_confirmation_message(raw) == (MS, True)


@pytest.mark.unit
def test_deregistration_from_network_matches_dmrtc_parser() -> None:
    raw = build_deregistration([MS], TS)
    assert raw == b"DMRT\x42" + TS.to_bytes(8, "little") + b"\x01" + bytes_3(MS) and len(raw) == 17
    assert dmrtc.validate_net_message(raw)
    assert dmrtc.parse_deregistration_confirmation_message(raw) == MS


@pytest.mark.unit
def test_subscription_confirmation_zeroes_refused_tgs() -> None:
    raw = build_group_confirmation(0x43, [91, 0])
    assert raw == bytes.fromhex("444d5254430200005b000000")
    assert dmrtc.validate_net_message(raw)
    assert dmrtc.parse_subscription_confirmation_message(raw) == [91, 0]


@pytest.mark.unit
@pytest.mark.parametrize(("op", "code"), [(0x43, 0x43), (0x44, 0x44), (DmrtOp.GROUP_SUB, 0x43), (DmrtOp.GROUP_UNSUB, 0x44)])
def test_group_confirmation_opcode(op: int, code: int) -> None:
    raw = build_group_confirmation(op, [91])
    assert raw[4] == code and dmrtc.parse_subscription_confirmation_message(raw) == [91]


@pytest.mark.unit
def test_group_confirmation_of_64_tgs_parses() -> None:
    tgs = list(range(100, 164))
    assert dmrtc.parse_subscription_confirmation_message(build_group_confirmation(0x43, tgs)) == tgs


@pytest.mark.unit
def test_with_network_opcode_keeps_the_message_verbatim() -> None:
    raw = dmrtc.create_udt_transfer_message(MS, 91, b"hello", 4, True, UUID)
    out = with_network_opcode(raw)
    assert out[4] == 0x45 and out[:4] == DMRT and out[5:] == raw[5:]
    assert dmrtc.validate_net_message(out)
    parsed = dmrtc.parse_udt_transfer_message(out)
    assert (parsed["uuid"], parsed["group"], parsed["src"], parsed["dst"], parsed["message"]) == (
        UUID, True, MS, 91, b"hello",
    )
    assert with_network_opcode(out) == out                               # idempotent


@pytest.mark.unit
def test_with_network_opcode_udt_accept_parses() -> None:
    out = with_network_opcode(dmrtc.create_udt_accept_message(MS2, MS, UUID))
    assert out[4] == 0x46 and dmrtc.parse_udt_accept_message(out) == (MS2, MS, UUID)


@pytest.mark.unit
def test_private_call_reject_layout() -> None:
    raw = build_pc_reject(UUID, MS, MS2)
    assert raw == b"DMRT\x49" + UUID + bytes_3(MS) + bytes_3(MS2) and len(raw) == 27
    assert dmrtc.validate_net_message(raw)


@pytest.mark.unit
@pytest.mark.parametrize("build", [
    lambda: build_registration_confirmation([], [], TS),
    lambda: build_registration_confirmation([True] * 9, [MS] * 9, TS),
    lambda: build_registration_confirmation([True], [MS, MS2], TS),
    lambda: build_registration_confirmation([True], [0x1000000], TS),
    lambda: build_deregistration([], TS),
    lambda: build_group_confirmation(0x05, [91]),
    lambda: build_group_confirmation(0x43, []),
    lambda: build_group_confirmation(0x43, [91] * 65),
    lambda: build_pc_reject(b"\x00" * 15, MS, MS2),
    lambda: with_network_opcode(b"DMRD\x01"),
])
def test_builders_refuse_invalid_input(build) -> None:
    with pytest.raises(ValueError):
        build()


@pytest.mark.unit
def test_every_reply_fits_in_a_dmrt_datagram() -> None:
    for raw in (
        build_registration_confirmation([True] * 8, [MS] * 8, TS),
        build_deregistration([MS] * 8, TS),
        build_group_confirmation(0x43, [0xFFFFFF] * 64),
    ):
        assert len(raw) <= 255 and raw[4] & 0x40


# --- call UUIDs, talkgroups, opcodes --------------------------------------

@pytest.mark.unit
def test_derive_stream_uuid_is_stable_per_stream_and_v4_shaped() -> None:
    a = derive_stream_uuid(b"\x0a\x0b\x0c\x0d")
    assert a == derive_stream_uuid(b"\x0a\x0b\x0c\x0d") and len(a) == 16
    assert a != derive_stream_uuid(b"\x0a\x0b\x0c\x0e")
    assert a[6] >> 4 == 4 and a[8] >> 6 == 0b10                          # RFC 4122 version 4, variant
    assert any(derive_stream_uuid(b"\x00\x00\x00\x00"))


@pytest.mark.unit
@pytest.mark.parametrize(("tg", "valid"), [
    (0, False), (1, False), (9, False), (10, True), (91, True), (214, True), (3999, True),
    (4000, False), (5000, False), (9989, True), (9990, False), (9999, False), (10000, True),
    (0xFFFFFF, True), (0x1000000, False),
])
def test_trunk_tg_validity(tg: int, valid: bool) -> None:
    assert trunk_tg_is_valid(tg) is valid


@pytest.mark.unit
def test_echo_talkgroups() -> None:
    assert is_echo_tg(9990) and is_echo_tg(9999)
    assert not is_echo_tg(9989) and not is_echo_tg(10000)


@pytest.mark.unit
def test_login_opcodes_are_the_spec_names() -> None:
    assert (DTCL, DTCK, DTCC, DTCCL, DTCO, DTCG) == (b"DTCL", b"DTCK", b"DTCC", b"DTCCL", b"DTCO", b"DTCG")
    assert (DTCPING, DTCP, DTCACK, DTCNAK, DTCPONG, DTCSBKN) == (
        b"DTCPING", b"DTCP", b"DTCACK", b"DTCNAK", b"DTCPONG", b"DTCSBKN",
    )
    assert DTCPING[:4] == DTCP and DTCCL[:4] == DTCC


@pytest.mark.unit
def test_hbp_constants_reexport_the_trunking_opcodes() -> None:
    from adn_server.infrastructure import hbp_constants

    assert hbp_constants.DTCL is DTCL and hbp_constants.DMRT is DMRT and hbp_constants.DTCPONG is DTCPONG
