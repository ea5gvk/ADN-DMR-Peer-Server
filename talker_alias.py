###############################################################################
#   ADN DMR Peer Server - DMR Talker Alias (ETSI / MMDVMHost)
#
#   Talker Alias (ETSI TS 102 361-2): short alphanumeric label carried in the
#   DMR voice stream. Two transports are supported here:
#     1) Standalone HBP "DMRA" UDP packets (15 bytes, up to 4 blocks per TX).
#     2) Embedded LC inside the forwarded DMRD voice (FLCO 4-7, bursts B-E),
#        which is what MMDVMHost / Pi-Star actually decode.
#
#   Ported from the clean-architecture implementation in the ce5rpy fork to
#   this monolithic codebase. UTF-8 encoding (format 2), same as MMDVMHost
#   DMRTA.cpp. Everything here is opt-in via the GLOBAL TALKER_ALIAS switch;
#   when disabled the bridge forwarding path behaves exactly as before.
###############################################################################

TALKER_ALIAS_MAX_LEN = 29

TA_FORMAT_7BIT = 0
TA_FORMAT_ISO8 = 1
TA_FORMAT_UTF8 = 2

DMRA_OPCODE = b'DMRA'
DMRA_PACKET_LEN = 15
DMRA_BLOCK_COUNT = 4
DMRA_PAYLOAD_LEN = 7
DMRA_BUF_LEN = DMRA_BLOCK_COUNT * DMRA_PAYLOAD_LEN  # 28

FLCO_TALKER_ALIAS_HEADER = 4  # FLCO 4 = TA block 0; 5,6,7 = blocks 1,2,3

VALID_MODES = frozenset({'inject', 'passthrough', 'both'})


def _int_id(_bytes):
    return int.from_bytes(_bytes, 'big')


# ---------------------------------------------------------------------------
# Encode / decode
# ---------------------------------------------------------------------------

def truncate_talker_alias(text):
    """Hard-cap at protocol maximum (not user-configurable)."""
    if len(text) <= TALKER_ALIAS_MAX_LEN:
        return text
    return text[:TALKER_ALIAS_MAX_LEN]


def decode_ta(buf):
    """Decode a 28-byte TA buffer (formats 0-3). Mirrors MMDVMHost CDMRTA::decodeTA."""
    if len(buf) < 1:
        return ''
    ta_format = (buf[0] >> 6) & 0x03
    ta_size = (buf[0] >> 1) & 0x1F
    if ta_format == TA_FORMAT_ISO8 or ta_format == TA_FORMAT_UTF8:
        raw = buf[1:1 + ta_size]
        return raw.decode('latin-1', errors='replace')
    if ta_format != TA_FORMAT_7BIT:
        return ''
    out = bytearray()
    t1 = 0
    t2 = 0
    c = 0
    for i in range(min(32, len(buf))):
        if t2 >= ta_size:
            break
        for j in range(7, -1, -1):
            c = ((c << 1) | ((buf[i] >> j) & 1)) & 0xFF
            t1 += 1
            if t1 == 7:
                if i > 0:
                    out.append(c & 0x7F)
                    t2 += 1
                    if t2 >= ta_size:
                        break
                t1 = 0
                c = 0
    return out[:ta_size].decode('ascii', errors='replace')


def encode_utf8(text):
    """Encode text into a 28-byte TA buffer (format 2 / UTF-8)."""
    text = truncate_talker_alias(text)
    raw = text.encode('utf-8')
    if len(raw) > 27:
        raw = text.encode('utf-8')[:27].decode('utf-8', errors='ignore').encode('utf-8')
    size = len(raw)
    header = (TA_FORMAT_UTF8 << 6) | (size << 1) | 0
    buf = bytearray(DMRA_BUF_LEN)
    buf[0] = header
    buf[1:1 + size] = raw
    return bytes(buf)


def encode_iso8(text):
    """Encode text into a 28-byte TA buffer (format 1 / ISO-8859-1).

    More widely displayed than UTF-8 on some radios (notably Hytera, which often
    will not render format 2 / UTF-8 Talker Alias)."""
    text = truncate_talker_alias(text)
    raw = text.encode('latin-1', errors='replace')[:27]
    size = len(raw)
    header = (TA_FORMAT_ISO8 << 6) | (size << 1) | 0
    buf = bytearray(DMRA_BUF_LEN)
    buf[0] = header
    buf[1:1 + size] = raw
    return bytes(buf)


def encode_7bit(text):
    """Encode text into a 28-byte TA buffer (format 0 / 7-bit packed).

    Bit stream is format(2) + size(5) + 7 bits per char, MSB-first; this is the
    exact inverse of decode_ta()'s 7-bit path. For the oldest radios."""
    text = truncate_talker_alias(text)
    chars = text.encode('ascii', errors='replace')[:31]
    size = len(chars)
    bits = [(TA_FORMAT_7BIT >> 1) & 1, TA_FORMAT_7BIT & 1]
    for b in range(4, -1, -1):
        bits.append((size >> b) & 1)
    for ch in chars:
        for b in range(6, -1, -1):
            bits.append((ch >> b) & 1)
    total = DMRA_BUF_LEN * 8
    bits = (bits + [0] * total)[:total]
    buf = bytearray(DMRA_BUF_LEN)
    for i, bit in enumerate(bits):
        if bit:
            buf[i // 8] |= 1 << (7 - (i % 8))
    return bytes(buf)


_TA_ENCODERS = {'utf8': encode_utf8, 'iso8': encode_iso8, '7bit': encode_7bit}


def encode_ta_buffer(text, text_format='utf8'):
    """Encode text into a 28-byte TA buffer in the requested format.
    text_format: 'utf8' (default), 'iso8' (ISO-8859-1, best for Hytera), '7bit'."""
    return _TA_ENCODERS.get(text_format, encode_utf8)(text)


def blocks_from_buffer(buf):
    """Split a 28-byte encoded buffer into four 7-byte DMRA payloads."""
    buf = buf.ljust(DMRA_BUF_LEN, b'\x00')[:DMRA_BUF_LEN]
    return [buf[i * DMRA_PAYLOAD_LEN:(i + 1) * DMRA_PAYLOAD_LEN] for i in range(DMRA_BLOCK_COUNT)]


def required_ta_block_count(buf):
    """Number of blocks 1-4 actually needed (skip trailing zero payloads)."""
    blocks = blocks_from_buffer(buf)
    last = 0
    for i in range(DMRA_BLOCK_COUNT):
        if blocks[i] != b'\x00' * DMRA_PAYLOAD_LEN:
            last = i
    return max(1, last + 1)


def buffer_from_blocks(blocks):
    """Merge up to four block payloads (dict block_id->payload) into a 28-byte buffer."""
    buf = bytearray(DMRA_BUF_LEN)
    for block_id, payload in blocks.items():
        if 0 <= block_id < DMRA_BLOCK_COUNT and payload:
            start = block_id * DMRA_PAYLOAD_LEN
            buf[start:start + DMRA_PAYLOAD_LEN] = payload[:DMRA_PAYLOAD_LEN]
    return bytes(buf)


# ---------------------------------------------------------------------------
# Standalone DMRA packet builders / parser
# ---------------------------------------------------------------------------

def build_dmra_packets(rf_src, text, text_format='utf8'):
    """Build the 1-4 HBP DMRA packets needed to carry 'text' (server injection)."""
    rf = rf_src[:3] if len(rf_src) >= 3 else rf_src.ljust(3, b'\x00')[:3]
    encoded = encode_ta_buffer(text, text_format)
    blocks = blocks_from_buffer(encoded)
    count = required_ta_block_count(encoded)
    packets = []
    for block_id in range(count):
        packets.append(DMRA_OPCODE + rf + bytes([block_id]) + blocks[block_id])
    return packets


def build_dmra_packet(rf_src, block_id, payload):
    """Build one 15-byte DMRA packet (pass-through of a buffered block)."""
    rf = rf_src[:3] if len(rf_src) >= 3 else rf_src.ljust(3, b'\x00')[:3]
    block = max(0, min(3, int(block_id)))
    pl = payload[:DMRA_PAYLOAD_LEN].ljust(DMRA_PAYLOAD_LEN, b'\x00')
    return DMRA_OPCODE + rf + bytes([block]) + pl


def parse_dmra_packet(data):
    """Parse a DMRA packet; returns (rf_src, block_id, payload) or None."""
    if len(data) < DMRA_PACKET_LEN or data[:4] != DMRA_OPCODE:
        return None
    rf_src = data[4:7]
    block_id = data[7]
    payload = data[8:15]
    return rf_src, block_id, payload


# ---------------------------------------------------------------------------
# Embedded LC (FLCO 4-7) builders
# ---------------------------------------------------------------------------

def talker_alias_lc_bytes(block_id, payload7):
    """9-byte embedded LC for one TA fragment (FLCO 4-7 + FID 0x00 + 7 payload)."""
    block = max(0, min(3, int(block_id)))
    payload = payload7[:DMRA_PAYLOAD_LEN].ljust(DMRA_PAYLOAD_LEN, b'\x00')
    return bytes([FLCO_TALKER_ALIAS_HEADER + block, 0x00]) + payload


def _encode_emblc(_lc):
    """Spec-correct embedded-LC encoder (BPTC 128/72 + 5-bit checksum).

    dmr_utils3.bptc.encode_emblc has a transcription bug: in burst D it uses
    _binlc[24] twice instead of _binlc[25], flipping one bit of byte 2 of every
    embedded LC. Harmless for the group voice LC (the bit lands in Service
    Options) but for Talker Alias byte 2 is the TA header (format/length) of
    block 0 and the first text char of blocks 1-3, so it corrupts the alias.
    The column-major interleave is: out bit (burst b, subrow j, row r) =
    _binlc[(b*4 + j) + 16*r], for b,j in 0..3 and r in 0..7.
    """
    from dmr_utils3 import hamming, crc
    from bitarray import bitarray
    _csum = crc.csum5(_lc)
    _binlc = bitarray(endian="big")
    _binlc.frombytes(_lc)
    _binlc.insert(32, _csum[0])
    _binlc.insert(43, _csum[1])
    _binlc.insert(54, _csum[2])
    _binlc.insert(65, _csum[3])
    _binlc.insert(76, _csum[4])
    for index in range(0, 112, 16):
        for hindex, hbit in zip(range(index + 11, index + 16),
                                hamming.enc_16114(_binlc[index:index + 11])):
            _binlc.insert(hindex, hbit)
    for index in range(0, 16):
        _binlc.insert(index + 112,
                      _binlc[index] ^ _binlc[index + 16] ^ _binlc[index + 32]
                      ^ _binlc[index + 48] ^ _binlc[index + 64] ^ _binlc[index + 80]
                      ^ _binlc[index + 96])
    _idx = [(b * 4 + j) + 16 * r for b in range(4) for j in range(4) for r in range(8)]
    out = bitarray(128, endian="big")
    for k, i in enumerate(_idx):
        out[k] = _binlc[i]
    return {1: out[0:32], 2: out[32:64], 3: out[64:96], 4: out[96:128]}


def encode_talker_alias_emblc(text, text_formats=('utf8',)):
    """Embedded-LC dicts for the TA blocks. `text_formats` may be a single
    encoding or several; with several, the TA is emitted in each encoding back to
    back (e.g. UTF-8 then ISO-8859-1) so radios of different vendors each pick up
    the format they support. Returns (list-of-emblc-dicts, total-count)."""
    if isinstance(text_formats, str):
        text_formats = [text_formats]
    emblcs = []
    for tf in (list(text_formats) or ['utf8']):
        encoded = encode_ta_buffer(text, tf)
        blocks = blocks_from_buffer(encoded)
        cnt = required_ta_block_count(encoded)
        emblcs += [_encode_emblc(talker_alias_lc_bytes(i, blocks[i])) for i in range(cnt)]
    return emblcs, len(emblcs)


def encode_talker_alias_emblc_from_blocks(blocks):
    """Build embedded TA LC dicts from buffered (passthrough) DMRA payloads."""
    buf = buffer_from_blocks(blocks)
    buf_blocks = blocks_from_buffer(buf)
    count = required_ta_block_count(buf)
    emblcs = [_encode_emblc(talker_alias_lc_bytes(i, buf_blocks[i])) for i in range(count)]
    return emblcs, count


# ---------------------------------------------------------------------------
# Policy: settings, text formatting, inject vs passthrough
# ---------------------------------------------------------------------------

def load_ta_profiles(json_path):
    """Build {id: {callsign, fname, surname, city, state, country}} from a
    radioid-style subscriber_ids.json. Lets inject-mode templates use name/QTH.
    Returns {} on any error (caller can fall back to a callsign-only map)."""
    import json
    out = {}
    try:
        with open(json_path, "r", encoding="latin-1") as fh:
            data = json.load(fh)
        for rec in data.get("results", []):
            try:
                rid = int(rec["id"])
            except (KeyError, ValueError, TypeError):
                continue
            out[rid] = {
                "callsign": (rec.get("callsign") or "").strip(),
                "fname": (rec.get("fname") or "").strip(),
                "surname": (rec.get("surname") or "").strip(),
                "city": (rec.get("city") or "").strip(),
                "state": (rec.get("state") or "").strip(),
                "country": (rec.get("country") or "").strip(),
            }
    except Exception:
        return {}
    return out


def ta_settings(CONFIG, system_name=None):
    """Effective Talker Alias settings (GLOBAL with optional per-system override)."""
    g = CONFIG.get('GLOBAL', {})
    sys_cfg = CONFIG.get('SYSTEMS', {}).get(system_name, {}) if system_name else {}
    enabled = sys_cfg.get('TALKER_ALIAS')
    if enabled is None:
        enabled = g.get('TALKER_ALIAS', False)
    mode = sys_cfg.get('TALKER_ALIAS_MODE')
    if mode is None:
        mode = g.get('TALKER_ALIAS_MODE', 'both')
    if mode not in VALID_MODES:
        mode = 'both'
    fmt = sys_cfg.get('TALKER_ALIAS_FORMAT')
    if fmt is None:
        fmt = g.get('TALKER_ALIAS_FORMAT', '{callsign} {fname}')
    tfmt = sys_cfg.get('TALKER_ALIAS_TEXT_FORMAT')
    if tfmt is None:
        tfmt = g.get('TALKER_ALIAS_TEXT_FORMAT', 'utf8')
    # comma-separated list -> emit the TA in each encoding back to back, so radios
    # of different vendors each pick up the format they support (e.g. 'utf8,iso8'
    # for Motorola + Hytera coexistence).
    formats = [f.strip().lower() for f in str(tfmt).split(',')]
    formats = [f for f in formats if f in ('utf8', 'iso8', '7bit')]
    if not formats:
        formats = ['utf8']
    return {'enabled': bool(enabled), 'mode': mode, 'format': str(fmt), 'text_formats': formats}


def ta_enabled(CONFIG, system_name=None):
    return ta_settings(CONFIG, system_name)['enabled']


def format_ta_text(CONFIG, rf_src):
    """Build the display string from the subscriber profile + template."""
    settings = ta_settings(CONFIG)
    template = settings['format']
    rid = _int_id(rf_src)
    profiles = CONFIG.get('_TA_PROFILES', {})
    profile = profiles.get(rid, {})
    callsign = profile.get('callsign') or ''
    fname = profile.get('fname') or ''
    surname = profile.get('surname') or ''
    city = profile.get('city') or ''
    state = profile.get('state') or ''
    country = profile.get('country') or ''
    if profile.get('talker_alias'):
        text = str(profile['talker_alias'])
    else:
        try:
            text = template.format(callsign=callsign, fname=fname,
                                   surname=surname, city=city, state=state,
                                   country=country, id=rid)
        except (KeyError, ValueError, IndexError):
            text = callsign or 'DMR ID:{}'.format(rid)
    text = ' '.join(text.split())
    if not text.strip():
        text = callsign or 'DMR ID:{}'.format(rid)
    return truncate_talker_alias(text)


def required_blocks_from_header(block0):
    """Blocks (1-4) needed for a TA whose header is in block 0's first byte."""
    if not block0:
        return DMRA_BLOCK_COUNT
    header = block0[0]
    ta_format = (header >> 6) & 0x03
    ta_size = (header >> 1) & 0x1F
    if ta_format in (TA_FORMAT_ISO8, TA_FORMAT_UTF8):
        needed_bytes = 1 + ta_size            # header byte + payload bytes
    else:
        needed_bytes = 1 + (((ta_size * 7) + 7) // 8)  # 7-bit packed chars
    count = (needed_bytes + DMRA_PAYLOAD_LEN - 1) // DMRA_PAYLOAD_LEN
    return max(1, min(DMRA_BLOCK_COUNT, count))


def passthrough_complete(blocks):
    """True when every TA block implied by the header (block 0) was received."""
    if not blocks or 0 not in blocks:
        return False
    block0 = blocks[0]
    if not block0 or len(block0) < 1:
        return False
    needed = required_blocks_from_header(block0)
    return all(i in blocks and blocks[i] and len(blocks[i]) >= DMRA_PAYLOAD_LEN for i in range(needed))


def passthrough_packets_from_blocks(rf_src, blocks):
    """Rebuild four DMRA packets from buffered block payloads."""
    packets = []
    for block_id in range(DMRA_BLOCK_COUNT):
        payload = blocks.get(block_id, b'\x00' * DMRA_PAYLOAD_LEN)
        packets.append(build_dmra_packet(rf_src, block_id, payload))
    return packets


def resolve_ta(CONFIG, system_name, rf_src, blocks):
    """Decide what TA to emit for this stream.

    Returns a dict {'source': 'passthrough'|'inject', 'text': str,
    'blocks': dict|None} or None when TA is disabled / nothing to send.
    """
    settings = ta_settings(CONFIG, system_name)
    if not settings['enabled']:
        return None
    mode = settings['mode']
    have_passthrough = passthrough_complete(blocks)
    if mode == 'passthrough':
        if not have_passthrough:
            return None
        return {'source': 'passthrough', 'blocks': dict(blocks),
                'text': decode_ta(buffer_from_blocks(blocks))}
    if mode == 'inject':
        return {'source': 'inject', 'blocks': None,
                'text': format_ta_text(CONFIG, rf_src), 'text_formats': settings['text_formats']}
    # both
    if have_passthrough:
        return {'source': 'passthrough', 'blocks': dict(blocks),
                'text': decode_ta(buffer_from_blocks(blocks))}
    return {'source': 'inject', 'blocks': None,
            'text': format_ta_text(CONFIG, rf_src), 'text_formats': settings['text_formats']}


def ta_dmra_packets(rf_src, resolved):
    """Standalone DMRA packets for a resolved TA (or [] )."""
    if not resolved:
        return []
    if resolved['source'] == 'passthrough':
        return passthrough_packets_from_blocks(rf_src, resolved['blocks'])
    return build_dmra_packets(rf_src, resolved['text'], (resolved.get('text_formats') or ['utf8'])[0])


def ta_emblc(resolved):
    """Embedded-LC (dicts, count) for a resolved TA, or None."""
    if not resolved:
        return None
    if resolved['source'] == 'passthrough':
        return encode_talker_alias_emblc_from_blocks(resolved['blocks'])
    return encode_talker_alias_emblc(resolved['text'], resolved.get('text_formats', ['utf8']))


# ---------------------------------------------------------------------------
# Per-stream embedded-LC state helpers (stored inside the target STATUS dict)
# ---------------------------------------------------------------------------

def init_embed_state(st, CONFIG, system_name, rf_src, stream_id, blocks):
    """Prepare alternating embedded TA state on a target stream status dict.

    Returns the resolved TA dict (so callers can also emit standalone DMRA),
    or None when TA is disabled / nothing to inject.
    """
    clear_embed_state(st)
    resolved = resolve_ta(CONFIG, system_name, rf_src, blocks)
    emb = ta_emblc(resolved)
    if emb:
        emblcs, count = emb
        st['TA_EMB'] = emblcs
        st['TA_COUNT'] = count
        st['TA_PHASE'] = 0
        # First B-E supercycle carries the normal group LC; TA on the next one.
        st['TA_ON'] = False
    return resolved


def clear_embed_state(st):
    for k in ('TA_EMB', 'TA_PHASE', 'TA_ON', 'TA_COUNT'):
        st.pop(k, None)


def rewrite_embed_lc(dmrbits, st, dtype_vseq, emb_key):
    """Return dmrbits with the embedded LC rewritten on bursts B-E (dtype 1-4),
    alternating one supercycle of group LC and one supercycle of TA blocks.

    Only call this when st has 'TA_EMB' set; otherwise use the legacy path.
    """
    ta_emb = st.get('TA_EMB')
    if ta_emb is not None and st.get('TA_ON'):
        phase = st.get('TA_PHASE', 0)
        count = st.get('TA_COUNT', DMRA_BLOCK_COUNT)
        frag = ta_emb[phase][dtype_vseq]
        if dtype_vseq == 4:
            st['TA_ON'] = False
            st['TA_PHASE'] = (phase + 1) % max(1, count)
    else:
        frag = st[emb_key][dtype_vseq]
        if dtype_vseq == 4 and ta_emb is not None:
            st['TA_ON'] = True
    return dmrbits[0:116] + frag + dmrbits[148:264]
