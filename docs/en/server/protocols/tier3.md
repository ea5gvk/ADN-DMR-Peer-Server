# DMR Tier III — Homebrew DMR trunking protocol v0.6

## Role

The **Homebrew DMR trunking protocol v0.6** (DL5ABM / YO8RZZ) links a **DMR Tier III site** to a network server. The site is dmrtc behind DMRGateway with `TrunkingEnabled=1`. It is HBP with three changes:

1. every login and keepalive opcode is renamed (`DTC*`);
2. `DMRD` frames carry a 16-byte **call UUID** (71 bytes);
3. a new **`DMRT`** message carries signalling: radio registrations, talkgroup subscriptions, UDT short data, private-call setup and status.

The server speaks it on every MASTER with `TRUNKING: true`, and only there. Setup and behaviour for operators: [DMR Tier III trunking](../user-guide/tier3-trunking.md).

## Login and keepalive

Layouts and lengths are those of the classic HBP twin; only the name changes.

| Site sends | Length | Server answers | Classic twin |
|------------|--------|----------------|--------------|
| `DTCL` + site ID (4) | 8 | `DTCACK` + salt (4) | `RPTL` |
| `DTCK` + site ID + SHA-256(salt ‖ password) | 40 | `DTCACK` + site ID, or `DTCNAK` + site ID | `RPTK` |
| `DTCC` + site ID + configuration (294) | 302 | `DTCACK` + site ID | `RPTC` |
| `DTCO` + site ID + options text | variable | `DTCACK` + site ID | `RPTO` |
| `DTCPING` + site ID | 11 | `DTCPONG` + site ID | `RPTPING` / `MSTPONG` |
| `DTCCL` + site ID | 9 | nothing | `RPTCL` |
| `DTCG` + site ID + `"%+08.4f%+09.4f"` | 25 | nothing | `RPTG` |

Server-initiated:

| Server sends | When |
|--------------|------|
| `DTCCL` + site ID | Site timed out, system removed or restarted. The site logs in again. |
| `DTCNAK` + site ID | Login refused (password, `REG_ACL`, unknown ID, `MAX_PEERS`). The site restarts its login. |

What the server does on a trunking MASTER:

- The final `DTCACK` (after `DTCC`) matters. DMRGateway then injects "MasterLogin" towards dmrtc, and dmrtc sends its subscriptions.
- `DTCL` marks the peer as a site. A `DTCK`, `DTCC` or `DTCPING` from a peer that is not marked gets `DTCNAK`.
- Classic `RPTL`, `RPTK`, `RPTC`, `RPTO` and `RPTPING` get `MSTNAK`.
- A `DTCCL` from a site gets no answer. DMRGateway may reopen its socket on the same port, and a late `DTCNAK` would restart that new login.
- `DTCO` is stored on the peer for the record (`TRUNK_OPTIONS`) and never applied as hotspot OPTIONS.
- `DTCG` updates the site's latitude and longitude. `DMRG` and `RPTG` are ignored.
- `DTCSBKN` (beacon request) is not sent.
- On a system without `TRUNKING`, every `DTC*` opcode is logged as `Unrecognized command`, as before.

## DMRD (71 bytes)

| Bytes | Field |
|-------|-------|
| 0–3 | `DMRD` |
| 4 | Sequence |
| 5–7 | Source ID |
| 8–10 | Destination (TG or unit) |
| 11–14 | Site ID |
| 15 | Bits: slot (0x80), unit call (0x40), frame type (0x30), data type / voice sequence (0x0F) |
| 16–19 | Stream ID |
| 20–52 | DMR payload (33 bytes) |
| 53–54 | BER, RSSI |
| 55–70 | **Call UUID** (16 bytes) |

The UUID is the call's identity on the trunking side: dmrtc derives its stream ID from it. Every frame of a call carries the same UUID, and each call has its own. dmrtc ignores the slot bit and carries many calls at once over one link.

**From a site:** a frame of 71 bytes or more has its UUID remembered for its stream ID. Routing then gets the classic 55 bytes. Each uplink stream has its own RX state, not the slot's, so concurrent calls with the same slot bit do not disturb each other.

**To a site:** always 71 bytes. The server writes the site's ID at 11–14, pads BER / RSSI to 55 bytes and appends the stream's UUID:

- the UUID the originating site sent for that stream (kept for 60 s after its last frame);
- else a stable UUID: BLAKE2b-128 of `b"ADN-TIER3"` + stream ID, with the RFC 4122 version 4 and variant bits set.

Which sites receive a frame:

| Frame | Sites |
|-------|-------|
| Group, TG 9990–9999 | Only the site that last keyed that TG on this system (until 60 s after the last frame keyed or played back) |
| Other group | Every connected site of the system subscribed to the TG |
| Unit (private voice, data) | The site where the destination is registered on this system, unless `SUB_MAP` heard it later elsewhere; else its `SUB_MAP` site on this system; else none |

There is no broadcast, and no hotspot gate (OPTIONS, slot contention, `SINGLE`) applies. A site never receives its own frames back.

## DMRT

`DMRT` (4 bytes), then the opcode at byte 4, then its fields. Addresses are 24-bit big-endian. Timestamps are signed 64-bit little-endian Unix seconds. A message is at most 255 bytes.

Bit `0x40` of the opcode marks a message **from the network**. A site sends `0x01`–`0x0A`, and the server answers or forwards with `0x40` set. Opcodes `0x80` and above (`0xC1` MasterLogin, `0xC2` MasterClose, `0x81`–`0x87` channel control) stay between DMRGateway and dmrtc.

| Op | Name | Length (bytes) | Layout after byte 4 | Server action |
|----|------|----------------|---------------------|---------------|
| `0x01` | Registration | 14 + 3n (n = 1..8) | [5–12] timestamp, [13] n, [14…] radio IDs | Each radio accepted unless `SUB_ACL` denies it (ID 0 is refused). It is then at this site. If it was at another connected site, that site gets a `0x42` for it. Answer: one `0x41`. |
| `0x41` | Registration confirmation | 15 + 3n | [5–12] timestamp, [13] status bits (bit i = ID i, 1 = accepted), [14] n, [15…] IDs | Sent by the server. |
| `0x02` | De-registration | 14 + 3n | as `0x01` | Forgotten if the radio is at this site, with its `SUB_MAP` entry there. Always answered with `0x42` echoing the IDs. |
| `0x42` | De-registration from network | 14 + 3n | as `0x01` | Sent as the answer to `0x02`, and to the old site when a radio roams. |
| `0x03` | Group subscription | 6 + 3n (n = 1..64) | [5] n, [6…] TGs | Each TG accepted unless reserved, denied by the TG ACLs, or over 512 TGs for the site. Answer: `0x43` with each TG, `0` for each refused one. |
| `0x04` | Group un-subscription | 6 + 3n | as `0x03` | Each TG dropped. Answer: `0x44` echoing every TG. |
| `0x05` | UDT transfer | 29 + size (size ≤ 46) | [5–20] UUID, [21] type (0x80 group, 0x40 ack request, low nibble format), [22] size, [23–25] source, [26–28] destination, [29…] payload | Forwarded as `0x45`. Group: to every other site subscribed to the TG, on any trunking system. Private: to the destination radio's site. The UUID is kept for 60 s to route the accept. |
| `0x06` | UDT accept | 27 | [5–20] UUID, [21–23] source, [24–26] destination | Forwarded as `0x46` to the site that sent the UDT. |
| `0x07` | Private call setup | 28 | [5–20] UUID, [21] service options, [22–24] source, [25–27] destination | Forwarded as `0x47` to the called radio's site. When it is not registered at another connected site: `0x49` back to the caller. |
| `0x08` / `0x09` | Private call accept / reject | 27 | as `0x06` | Forwarded as `0x48` / `0x49` to the caller's site. |
| `0x0A` | Status transfer | 28 | [5–20] UUID, [21] 0x80 group + 7-bit status, [22–24] source, [25–27] destination | Forwarded as `0x4A`, routed like a UDT. No answer is tracked. |

Rules:

- The length must be exactly what dmrtc builds; anything else is dropped.
- A message from a site with bit `0x40` or `0x80` set, an unknown opcode, or a malformed message is dropped and logged at `DEBUG` once per site and opcode.
- An accept or reject is forwarded only for a UUID the server routed in the last 60 s, and only when it matches that UUID's kind (UDT or private call).
- Forwarded messages are the site's bytes with bit `0x40` added. They only go to connected sites and never back to the sender.
- `DMRT` carries no site ID. The server takes the site from the source address of the datagram, and accepts it only from a logged-in site (5–255 bytes).

**dmrtc today** sends `0x01`–`0x06` and handles `0x41`–`0x46`. Its controller compares incoming opcodes without the `0x40` bit, so it ignores `0x45` and `0x46`: SMS between sites does not reach RF until dmrtc is fixed. It does not use `0x07`–`0x0A` yet.

## State

One registry per process, outside the configuration. `SIGHUP` keeps it; a restart clears it.

| State | Lifetime |
|-------|----------|
| Radio location (one site per radio, across all trunking systems) | Until de-registration, roaming, or the system is removed. It survives the site logging in again. |
| Talkgroups per site | Until un-subscription or the site leaves |
| Stream UUIDs | 60 s after the stream's last frame |
| UDT / private-call UUIDs | 60 s |
| Echo source per system and TG | 60 s after the last frame |
| Per-stream RX state of a site | 60 s after the last frame |

When a site leaves (timeout, `DTCCL`, NAK, new login), its radios, talkgroups and streams are dropped. The talkgroups no other site of the system holds are released.

In routing, the first site of a system to subscribe a TG gives the system **TRUNK** legs on both slots. These are active, never timed out, and shown as static in the monitor. When the last site drops the TG, they go back to idle in-band legs.

## Implementation

| Layer | Module |
|-------|--------|
| Domain (wire format) | `domain/trunking.py` |
| Signalling and registry | `application/trunking/` (`use_cases.py`, `registry.py`, `config.py`) |
| Routing legs and unit routing | `application/routing/trunk_routing.py`, `application/subscription/trunk_legs_ops.py` |
| HBP MASTER side | `infrastructure/twisted_adapters/hbp_trunking.py` (mixed into `HBPProtocol`) |
| Wiring | `infrastructure/bootstrap/trunking.py` |

Tests: `tests/trunking/`. `tests/support/dmrtc_wire.py` ports dmrtc's message builders and parsers, so the tests use the client's own byte layout.

## References

- Protocol specification v0.6: `docs/Homebrew_DMR_trunking_protocol_v0.6.pdf` in the [dmrtc repository](https://github.com/qradiolink/dmrtc).
- Client: DMRGateway `DMRNetwork.cpp` (`TrunkingEnabled=1`); dmrtc `networksignalling.cpp` and `controller.cpp`.
- [HBP (DMRD)](hbp.md) for the classic frame.
