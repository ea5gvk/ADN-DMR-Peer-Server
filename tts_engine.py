#!/usr/bin/env python
#
###############################################################################
# Copyright (C) 2026 Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
#
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

'''
TTS Engine for ADN-DMR-Peer-Server
Converts text files (.txt) to AMBE audio files (.ambe) for DMR transmission.

Pipeline: .txt -> gTTS -> .mp3 -> ffmpeg -> .wav (8kHz mono 16-bit) -> vocoder -> .ambe

Encoding options (in priority order):
  1. AMBEServer (DV3000 remoto via UDP) - TTS_AMBESERVER_HOST/PORT in voice.cfg
  2. External vocoder command - TTS_VOCODER_CMD in voice.cfg
  3. Pre-converted .ambe file (bypass pipeline)

Requires:
  - gTTS (pip install gTTS)
  - ffmpeg (system package)
  - One of: AMBEServer, external vocoder, or pre-converted .ambe files
'''

import os
import socket
import struct
import subprocess
import wave
import logging

logger = logging.getLogger(__name__)

_LANG_MAP = {
    'es_ES': 'es', 'en_GB': 'en', 'en_US': 'en', 'fr_FR': 'fr',
    'de_DE': 'de', 'it_IT': 'it', 'pt_PT': 'pt', 'pt_BR': 'pt',
    'pl_PL': 'pl', 'nl_NL': 'nl', 'da_DK': 'da', 'sv_SE': 'sv',
    'no_NO': 'no', 'el_GR': 'el', 'th_TH': 'th', 'cy_GB': 'cy',
    'ca_ES': 'ca', 'gl_ES': 'gl', 'eu_ES': 'eu',
}

DV3K_START_BYTE = 0x61
DV3K_TYPE_CONTROL = 0x00
DV3K_TYPE_AMBE = 0x01
DV3K_TYPE_AUDIO = 0x02

DV3K_AMBE_FIELD_ID = 0x01
DV3K_AUDIO_FIELD_ID = 0x00

DV3K_SAMPLES_PER_FRAME = 160

DV3K_RATET_DMR = bytes([
    0x61, 0x00, 0x02, 0x00, 0x09, 0x21
])

DV3K_PRODID_REQ = bytes([0x61, 0x00, 0x01, 0x00, 0x30])


def _get_tts_lang(announcement_language):
    if announcement_language in _LANG_MAP:
        return _LANG_MAP[announcement_language]
    return announcement_language[:2]


def _generate_tts_audio(text, lang, mp3_path):
    try:
        from gtts import gTTS
    except ImportError:
        logger.error('(TTS) gTTS no esta instalado. Ejecuta: pip install gTTS')
        return False

    try:
        tts = gTTS(text=text, lang=lang, slow=False)
        tts.save(mp3_path)
        logger.info('(TTS) Audio TTS generado: %s', mp3_path)
        return True
    except Exception as e:
        logger.error('(TTS) Error generando audio TTS: %s', e)
        return False


def _convert_to_wav(mp3_path, wav_path, volume_db=0, speed=1.0):
    speed = max(0.5, min(2.0, speed))
    _filters = []
    if speed != 1.0:
        _filters.append('atempo={:.2f}'.format(speed))
        logger.info('(TTS) Aplicando velocidad: x%.2f', speed)
    if volume_db != 0:
        _filters.append('volume={}dB'.format(volume_db))
        logger.info('(TTS) Aplicando ajuste de volumen: %ddB', volume_db)
    _cmd = ['ffmpeg', '-y', '-i', mp3_path,
            '-ar', '8000', '-ac', '1', '-sample_fmt', 's16']
    if _filters:
        _cmd += ['-af', ','.join(_filters)]
    _cmd += ['-f', 'wav', wav_path]
    try:
        result = subprocess.run(_cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            logger.error('(TTS) Error ffmpeg: %s', result.stderr.decode('utf-8', errors='ignore')[:500])
            return False
        logger.info('(TTS) Audio convertido a WAV 8kHz mono: %s', wav_path)
        return True
    except FileNotFoundError:
        logger.error('(TTS) ffmpeg no encontrado. Instala ffmpeg en el sistema')
        return False
    except subprocess.TimeoutExpired:
        logger.error('(TTS) Timeout en conversion ffmpeg')
        return False
    except Exception as e:
        logger.error('(TTS) Error en conversion de audio: %s', e)
        return False


def _encode_ambe_vocoder(wav_path, ambe_path, vocoder_cmd):
    if not vocoder_cmd:
        return False

    cmd = vocoder_cmd.replace('{wav}', wav_path).replace('{ambe}', ambe_path)

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, timeout=120
        )
        if result.returncode != 0:
            logger.error('(TTS) Error del vocoder: %s', result.stderr.decode('utf-8', errors='ignore')[:500])
            return False
        if not os.path.isfile(ambe_path):
            logger.error('(TTS) El vocoder no genero el archivo AMBE: %s', ambe_path)
            return False
        logger.info('(TTS) Audio codificado a AMBE via vocoder externo: %s', ambe_path)
        return True
    except FileNotFoundError:
        logger.error('(TTS) Comando vocoder no encontrado: %s', cmd.split()[0] if cmd else '(vacio)')
        return False
    except subprocess.TimeoutExpired:
        logger.error('(TTS) Timeout en codificacion AMBE')
        return False
    except Exception as e:
        logger.error('(TTS) Error ejecutando vocoder: %s', e)
        return False


def _build_audio_packet(pcm_samples):
    payload = struct.pack('BB', DV3K_AUDIO_FIELD_ID, len(pcm_samples))
    for sample in pcm_samples:
        payload += struct.pack('>h', sample)
    header = bytes([DV3K_START_BYTE]) + struct.pack('>HB', len(payload), DV3K_TYPE_AUDIO)
    return header + payload


def _parse_ambe_response(data):
    if len(data) < 4:
        return None
    if data[0] != DV3K_START_BYTE:
        return None

    _payload_len = struct.unpack('>H', data[1:3])[0]
    _pkt_type = data[3]

    if _pkt_type == DV3K_TYPE_AMBE:
        _field_id = data[4]
        if _field_id == DV3K_AMBE_FIELD_ID:
            _num_bits = data[5]
            _num_bytes = (_num_bits + 7) // 8
            _ambe_data = data[6:6 + _num_bytes]
            return _ambe_data

    if _pkt_type == DV3K_TYPE_CONTROL:
        logger.debug('(TTS-AMBESERVER) Control response received (field_id: 0x%02X)', data[4] if len(data) > 4 else 0)

    return None


def _encode_ambe_ambeserver(wav_path, ambe_path, host, port):
    host = host.strip().strip('"').strip("'")
    logger.info('(TTS-AMBESERVER) Conectando a AMBEServer %s:%d', host, port)

    try:
        _resolved_ip = socket.gethostbyname(host)
        if _resolved_ip != host:
            logger.info('(TTS-AMBESERVER) Host %s resuelto a %s', host, _resolved_ip)
        host = _resolved_ip
    except socket.gaierror as e:
        logger.error('(TTS-AMBESERVER) No se puede resolver el host "%s": %s', host, e)
        return False

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5.0)
    except Exception as e:
        logger.error('(TTS-AMBESERVER) Error creando socket UDP: %s', e)
        return False

    try:
        sock.sendto(DV3K_PRODID_REQ, (host, port))
        data, addr = sock.recvfrom(1024)
        if data[0] != DV3K_START_BYTE:
            logger.error('(TTS-AMBESERVER) Respuesta invalida del AMBEServer')
            sock.close()
            return False
        logger.info('(TTS-AMBESERVER) AMBEServer conectado correctamente')
    except socket.timeout:
        logger.error('(TTS-AMBESERVER) Timeout conectando a AMBEServer %s:%d - Verifica que el servidor esta activo', host, port)
        sock.close()
        return False
    except Exception as e:
        logger.error('(TTS-AMBESERVER) Error conectando a AMBEServer: %s', e)
        sock.close()
        return False

    try:
        sock.sendto(DV3K_RATET_DMR, (host, port))
        data, addr = sock.recvfrom(1024)
        if data[0] != DV3K_START_BYTE:
            logger.error('(TTS-AMBESERVER) Error configurando RATET DMR')
            sock.close()
            return False
        logger.info('(TTS-AMBESERVER) RATET DMR (AMBE+2 tabla 33) configurado')
    except socket.timeout:
        logger.error('(TTS-AMBESERVER) Timeout configurando RATET')
        sock.close()
        return False

    try:
        wf = wave.open(wav_path, 'rb')
    except Exception as e:
        logger.error('(TTS-AMBESERVER) Error abriendo WAV: %s', e)
        sock.close()
        return False

    if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
        logger.error('(TTS-AMBESERVER) WAV debe ser mono 16-bit PCM')
        wf.close()
        sock.close()
        return False

    _total_frames = wf.getnframes()
    _sample_rate = wf.getframerate()
    logger.info('(TTS-AMBESERVER) WAV: %d muestras, %d Hz, duracion: %.1f s',
                _total_frames, _sample_rate, _total_frames / _sample_rate)

    _raw_frames = wf.readframes(_total_frames)
    wf.close()

    _samples = list(struct.unpack('<' + 'h' * (_total_frames), _raw_frames))

    _ambe_frames = []
    _frames_sent = 0
    _frames_error = 0

    for i in range(0, len(_samples), DV3K_SAMPLES_PER_FRAME):
        _chunk = _samples[i:i + DV3K_SAMPLES_PER_FRAME]
        if len(_chunk) < DV3K_SAMPLES_PER_FRAME:
            _chunk = _chunk + [0] * (DV3K_SAMPLES_PER_FRAME - len(_chunk))

        _audio_pkt = _build_audio_packet(_chunk)

        try:
            sock.sendto(_audio_pkt, (host, port))
            data, addr = sock.recvfrom(1024)
            _ambe_data = _parse_ambe_response(data)
            if _ambe_data is not None:
                _ambe_frames.append(_ambe_data)
                _frames_sent += 1
            else:
                _frames_error += 1
                logger.debug('(TTS-AMBESERVER) Frame %d: respuesta no AMBE (tipo: 0x%02X)',
                             i // DV3K_SAMPLES_PER_FRAME, data[3] if len(data) > 3 else 0)
        except socket.timeout:
            _frames_error += 1
            logger.warning('(TTS-AMBESERVER) Timeout en frame %d', i // DV3K_SAMPLES_PER_FRAME)
        except Exception as e:
            _frames_error += 1
            logger.error('(TTS-AMBESERVER) Error en frame %d: %s', i // DV3K_SAMPLES_PER_FRAME, e)

    sock.close()

    if not _ambe_frames:
        logger.error('(TTS-AMBESERVER) No se recibieron frames AMBE')
        return False

    try:
        with open(ambe_path, 'wb') as f:
            for frame in _ambe_frames:
                f.write(frame)
    except Exception as e:
        logger.error('(TTS-AMBESERVER) Error escribiendo archivo AMBE: %s', e)
        return False

    logger.info('(TTS-AMBESERVER) Codificacion completada: %d frames AMBE (%d errores), guardado en %s',
                _frames_sent, _frames_error, ambe_path)
    return True


def text_to_ambe(txt_path, ambe_path, language, vocoder_cmd, ambeserver_host='', ambeserver_port=2460, volume_db=0, speed=1.0):
    if not os.path.isfile(txt_path):
        logger.warning('(TTS) Archivo de texto no encontrado: %s', txt_path)
        return False

    if os.path.isfile(ambe_path):
        txt_mtime = os.path.getmtime(txt_path)
        ambe_mtime = os.path.getmtime(ambe_path)
        if ambe_mtime > txt_mtime:
            logger.info('(TTS) Usando AMBE cacheado (mas reciente que .txt): %s', ambe_path)
            return True

    with open(txt_path, 'r', encoding='utf-8') as f:
        text = f.read().strip()

    if not text:
        logger.warning('(TTS) Archivo de texto vacio: %s', txt_path)
        return False

    logger.info('(TTS) Convirtiendo texto a AMBE: %s (%d caracteres, idioma: %s)', txt_path, len(text), language)

    _dir = os.path.dirname(ambe_path)
    if _dir:
        os.makedirs(_dir, exist_ok=True)

    _base = os.path.splitext(ambe_path)[0]
    _mp3_path = _base + '.mp3'
    _wav_path = _base + '.wav'

    _tts_lang = _get_tts_lang(language)

    if not _generate_tts_audio(text, _tts_lang, _mp3_path):
        return False

    if not _convert_to_wav(_mp3_path, _wav_path, volume_db, speed):
        _cleanup([_mp3_path])
        return False

    _encoded = False

    if ambeserver_host:
        logger.info('(TTS) Usando AMBEServer %s:%d para codificacion AMBE', ambeserver_host, ambeserver_port)
        _encoded = _encode_ambe_ambeserver(_wav_path, ambe_path, ambeserver_host, ambeserver_port)
        if not _encoded:
            logger.warning('(TTS) AMBEServer fallo, intentando vocoder externo...')

    if not _encoded and vocoder_cmd:
        logger.info('(TTS) Usando vocoder externo para codificacion AMBE')
        _encoded = _encode_ambe_vocoder(_wav_path, ambe_path, vocoder_cmd)

    if not _encoded:
        logger.warning('(TTS) No se pudo codificar a AMBE. Archivos intermedios disponibles:')
        logger.warning('(TTS)   MP3: %s', _mp3_path)
        logger.warning('(TTS)   WAV: %s', _wav_path)
        logger.warning('(TTS) Opciones para codificar:')
        logger.warning('(TTS)   1. Configura TTS_AMBESERVER_HOST en voice.cfg (DV3000 remoto)')
        logger.warning('(TTS)   2. Configura TTS_VOCODER_CMD en voice.cfg (vocoder local)')
        logger.warning('(TTS)   3. Convierte manualmente el WAV a AMBE y guardalo como: %s', ambe_path)
        return False

    _cleanup([_mp3_path, _wav_path])

    logger.info('(TTS) Conversion completada: %s -> %s', txt_path, ambe_path)
    return True


def _cleanup(files):
    for f in files:
        try:
            if os.path.isfile(f):
                os.remove(f)
        except Exception:
            pass


def ensure_tts_ambe(config, tts_num):
    _prefix = 'TTS_ANNOUNCEMENT{}'.format(tts_num)

    if not config['GLOBAL'].get('{}_ENABLED'.format(_prefix), False):
        return None

    _file = config['GLOBAL']['{}_FILE'.format(_prefix)]
    _lang = config['GLOBAL']['{}_LANGUAGE'.format(_prefix)]
    _vocoder_cmd = config['GLOBAL'].get('TTS_VOCODER_CMD', '')
    _ambeserver_host = config['GLOBAL'].get('TTS_AMBESERVER_HOST', '')
    _ambeserver_port = config['GLOBAL'].get('TTS_AMBESERVER_PORT', 2460)
    _volume_db = config['GLOBAL'].get('TTS_VOLUME', -3)
    _speed = config['GLOBAL'].get('TTS_SPEED', 1.0)

    _txt_path = './Audio/{}/ondemand/{}.txt'.format(_lang, _file)
    _ambe_path = './Audio/{}/ondemand/{}.ambe'.format(_lang, _file)

    if os.path.isfile(_ambe_path):
        if not os.path.isfile(_txt_path):
            logger.info('(TTS-%d) Usando archivo AMBE existente (sin .txt): %s', tts_num, _ambe_path)
            return _ambe_path
        txt_mtime = os.path.getmtime(_txt_path)
        ambe_mtime = os.path.getmtime(_ambe_path)
        if ambe_mtime > txt_mtime:
            logger.debug('(TTS-%d) Usando AMBE cacheado: %s', tts_num, _ambe_path)
            return _ambe_path

    if not os.path.isfile(_txt_path):
        logger.warning('(TTS-%d) Archivo de texto no encontrado: %s', tts_num, _txt_path)
        return None

    if text_to_ambe(_txt_path, _ambe_path, _lang, _vocoder_cmd, _ambeserver_host, _ambeserver_port, _volume_db, _speed):
        return _ambe_path
    else:
        if os.path.isfile(_ambe_path):
            logger.warning('(TTS-%d) Usando AMBE anterior (conversion fallo): %s', tts_num, _ambe_path)
            return _ambe_path
        return None
