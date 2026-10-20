# Test layout

One **topic per file** — run only what you need while developing or validating a change.

```bash
# Install into the pyenv site-packages (not ~/.local); Cursor/VS Code pytest sets PYTHONNOUSERSITE=1
python3 -m pip install --no-user -e ".[dev]"
python3 -m pytest tests/<path>/test_<name>.py -q          # single file
python3 -m pytest tests/<path>/test_<name>.py::test_foo -q # single test
python3 -m pytest tests/routing/ -q                        # whole domain
python3 -m pytest tests/ -q                                # full suite
python3 -m pytest tests/ -q -m "not mqtt"                 # skip MQTT-heavy tests
```

Use the project interpreter, e.g. `/opt/.pyenv/versions/3.11.8/bin/python3`.

## Directories

| Directory | What it covers |
|-----------|----------------|
| `routing/` | Static TG, startup subscriptions, unit data, CRC dedup, echo reset, private voice, config reload |
| `hbp/` | HBP ingress, loop control, rate limit, timeout/collision, master maintenance |
| `obp/` | OpenBridge loop, rate limit, unit-data loop |
| `voice/` | Announcements, TTS schedule, broadcast queue, disconnected voice, in-band signalling |
| `talker_alias/` | Encode/decode, passthrough, MMDVM wire, routing inject (DeterministicScenario) |
| `echo/` | Recording timers, playback loop, seq preservation, ingress path |
| `replay/` | JSONL session replay |
| `schemas/` | Report v2 JSON Schema validation (`jsonschema` dev dep) |
| `application/` | Report payloads, monitor topology, proxy use cases, subscription store/router |
| `fakes/` | Re-exports for application tests (`InMemorySubscriptionStore` shim) — not run as tests |
| `infrastructure/` | Logging reload, ACL router, **HBP REPEAT + proxy fan-in integration**, MQTT |
| `smoke/` | Quick routing smoke |
| `trunking/` | DMR Tier III: DMRT codec against dmrtc's wire format, trunking registry, DMRT signalling use cases, DTC* login and DMRT on the real HBP MASTER, `TRUNKING` flag (validation, reload), TRUNK store legs, downlink to the sites (71-byte DMRD) and uplink from them (per-stream RX rows) |
| `support/` | Shared stacks (`hbp_repeat_stack`, monitor sim, `dmrtc_wire` Tier III client mirror, `trunk_site_stack` Tier III sites on real MASTERs, `TrunkRoutingStack` with routing as bootstrap wires it) — not run as tests |
| `harness/` | Shared fakes (`DeterministicScenario`, assertions) — not run as tests |

## Integration vs harness

Most routing/voice tests inject packets via **`DeterministicScenario`** (`routing.dmrd_received` on fakes). That is fast but **skips** `udp_hbp` REPEAT rewrite and proxy UDP fan-in.

For regressions on those paths, use:

| File | Topic |
|------|-------|
| `infrastructure/test_hbp_repeat_talker_alias.py` | Real `HBPProtocol` REPEAT + embedded TA |
| `infrastructure/test_proxy_repeat_e2e.py` | Proxy fan-in → REPEAT downlink |
| `infrastructure/test_proxy_reload.py` | Hot reload keeps UDP listener |

Mark new stack tests with `@pytest.mark.integration`.

## Files by domain

### routing/

| File | Tests | Topic |
|------|-------|-------|
| `test_config_reload.py` | 1 | Merge system config on reload |
| `test_crc_dedup.py` | 3 | HBP/OBP CRC dedup, seq=0 |
| `test_echo_subscription_reset.py` | 5 | Echo leg after subscription reset / OPTIONS |
| `test_options_config_loop.py` | 3 | OPTIONS paths (RPTO/startup; no 26s loop) |
| `test_peer_options_override.py` | — | RPTO SINGLE/TIMER override, inject proxy |
| `test_private_voice.py` | 3 | Private call routing |
| `test_startup_subscriptions.py` | 4 | Startup subscriptions + voice E2E |
| `test_static_tg_options.py` | 4 | Static TG from peer OPTIONS |
| `test_subscription_router_dmrd.py` | — | `dmrd_received` via `SubscriptionRouter` |
| `test_unit_data_ingress.py` | 4 | Unit headers, CSBK, reports |
| `test_unit_data_routing.py` | 5 | SUB_MAP, hotspot, gateway, OBP fanout |

### hbp/

| File | Tests | Topic |
|------|-------|-------|
| `test_hbp_loop_control.py` | 2 | HBP loop winner/loser |
| `test_hbp_rate_limit.py` | 2 | Ingress rate drop + stall |
| `test_ingress.py` | 3 | RX start, rate drop, OBP loop loser |
| `test_master_maintenance.py` | 2 | Peer timeout / shared PEERS dict |
| `test_timeout_collision.py` | 3 | 180s timeout, collision, rekey |

### obp/

| File | Tests | Topic |
|------|-------|-------|
| `test_loop_control.py` | 4 | OBP loop, VTERM, BCSQ |
| `test_obp_rate_limit.py` | 1 | Rate limit epoch |
| `test_unit_data_loop.py` | 2 | Unit data loop loser |

### voice/

| File | Tests | Topic |
|------|-------|-------|
| `test_disconnected_voice.py` | 3 | Not-linked / reflector prompts |
| `test_in_band_signalling.py` | 5 | Reflector / single-mode VTERM |
| `test_play_file_on_request.py` | 3 | On-demand file playback |
| `test_prompt_holds_slot.py` | 4 | Server prompts hold TS2 while they play |

Scheduled announcements, TTS and beacons are tested with their plugin: `plugins/voice-announcements/tests/`.

### talker_alias/

| File | Tests | Topic |
|------|-------|-------|
| `test_routing_inject.py` | 3 | TA inject on routing VHEAD (harness) |
| `test_embed_ta.py` | 4 | Embedded LC modes |
| `test_encode_decode.py` | 8 | Domain encode/decode |
| `test_format.py` | 2 | Format from subscriber profile |
| `test_mmdvm_wire.py` | 10 | MMDVM wire blocks |
| `test_passthrough.py` | 9 | Passthrough / both modes |

### trunking/

| File | Tests | Topic |
|------|-------|-------|
| `test_trunk_dmrt_codec.py` | 89 | DMRT parse/build vs dmrtc builders and parsers, call UUID, TG validity |
| `test_trunk_registry.py` | 15 | MS locations, roaming, per-site TGs, stream UUIDs, pendings, echo, drop/TTL |
| `test_trunk_signalling.py` | 52 | Every DMRT opcode: registration/ACL, subscriptions, UDT, private call, status, malformed |
| `test_trunk_config_flag.py` | 9 | `system_is_trunking` |
| `test_trunk_login.py` | 25 | DTC* login / keepalive / close on the real MASTER, classic RPT* refused, DTCCL on timeout and dereg |
| `test_trunk_dmrt_ingress.py` | 11 | DMRT in by address, answers written to the right site, `send_trunking_message` guards |
| `test_trunk_config_validation.py` | 22 | `TRUNKING` validator rules, example yaml block |
| `test_trunk_config_reload.py` | 3 | SIGHUP flipping `TRUNKING`: remove + add; same flag keeps the protocol |
| `test_trunk_legs.py` | 17 | TRUNK legs follow the subscriptions; immune to rule timers, in-band SINGLE / TG 4000, OPTIONS, SIGHUP; BRIDGERESET resync |
| `test_trunk_egress.py` | 13 | DMRD the trunking MASTER writes: subscribed / echo (kept while the playback lasts) / registered site only, 71 bytes with the stream UUID, no hotspot gates; site to site only once routing accepts |
| `test_trunk_downlink.py` | 16 | Hotspot / OpenBridge group calls, concurrent calls, private voice and unit data reaching the sites end to end; a site's subscription never cuts a hotspot on the shared MASTER; a call losing its terminator after burst C still ends |
| `test_trunk_unit_location.py` | 8 | Where a unit is: registrations survive the site's re-login, a later sighting elsewhere wins, SUB_MAP entries on a trunking system age for the OpenBridge fan-out, de-registration releases the unit |
| `test_trunk_uplink.py` | 13 | Concurrent calls from one site on one slot (packet control, START/END, LC, durations per stream), site to site with the UUID, parrot, private calls, TG rule / loop control / loop guard seeing site streams, row cleanup |

### echo/

| File | Tests | Topic |
|------|-------|-------|
| `test_playback_ingress.py` | 2 | `ingress_pkt_time`, record→playback |
| `test_playback_logging.py` | 1 | Duration log format |
| `test_playback_send_loop.py` | 4 | Send loop, max duration, interval |
| `test_recording_timers.py` | 2 | Idle timeout, VTERM commit |
| `test_rekey_playback.py` | 4 | Seq preservation past 255 / 30s |

### infrastructure (integration highlights)

| File | Topic |
|------|-------|
| `test_hbp_repeat_talker_alias.py` | REPEAT embed TA through real HBP |
| `test_proxy_repeat_e2e.py` | Proxy → REPEAT E2E |
| `test_proxy_reload.py` | Proxy hot reload |
| `test_udp_fanin.py` | UDP fan-in routing |
| `test_report_server_wire.py` | Report server wire opcodes |
| `test_logging_reload.py` | Log level reload |
| `test_acl_router.py` | ACL range checks (`acl_check` parity) |

### smoke/ · application/

| File | Topic |
|------|-------|
| `smoke/test_routing.py` | Static TG forward smoke |
| `application/test_monitor_topology.py` | Inject-only proxy monitor remap |
| `application/test_runtime_context.py` | RuntimeContext holder, SIGHUP swap prep |
| `application/test_subscription_router.py` | `SubscriptionRouter` vs legacy scan |
| `application/test_routing_table_export.py` | Monitor export shim from store |

## Harness API (post-rename)

| Symbol | Role |
|--------|------|
| `DeterministicScenario` | Wires `RoutingUseCases` + `InMemorySubscriptionStore` + `InMemoryAclRouter` |
| `scenario.routing` | Use-case facade (`dmrd_received`, timers, OPTIONS) |
| `scenario.seed_routing_table()` | Seed store from legacy monitor-shaped dict |
| `active_routing_table()` | Build minimal ACTIVE routing table for harness |
| `patch_routing_wall_time()` | Patches wall clock on OBP/unit paths |

Session replay JSONL meta accepts `routing_table` (preferred) or legacy `bridges`; `apply_startup_subscriptions` or legacy `apply_startup_bridges`.

## Examples (copy-paste)

```bash
# After changing unit-data routing
python3 -m pytest tests/routing/test_unit_data_routing.py -q

# After echo seq fix
python3 -m pytest tests/echo/test_rekey_playback.py -q

# Talker Alias REPEAT (real stack)
python3 -m pytest tests/infrastructure/test_hbp_repeat_talker_alias.py -q

# HBP loop + rate (common RF regressions)
python3 -m pytest tests/hbp/test_hbp_loop_control.py tests/hbp/test_hbp_rate_limit.py -q

# One test by name
python3 -m pytest tests/routing/test_startup_subscriptions.py::test_startup_bridge_routes_voice_after_apply -q
```

## Policy

- **New tests:** add a new file (or extend the smallest existing file for the same topic). Avoid large multi-topic modules.
- **Harness:** shared code lives in `harness/` and `support/` only.
- **Stack regressions:** prefer `infrastructure/test_*_e2e.py` with real adapters over duplicating in `DeterministicScenario`.
- Full audit: `docs-priv/en/test-audit.md` (maintainer checkout).
