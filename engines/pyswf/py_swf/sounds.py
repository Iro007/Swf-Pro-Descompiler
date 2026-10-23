"""
Export de sonidos SWF (DefineSound y SoundStream*) a formatos estándar.

- MP3          -> passthrough .mp3
- PCM (0 y 3)  -> .wav (stdlib wave)
- ADPCM (1)    -> decodificado a PCM 16-bit -> .wav
- Nellymoser / Speex -> bytes crudos (.bin) con aviso en el nombre

Referencia: SWF File Format Spec v19, cap. "Sounds".
"""
import io
import struct
import wave

from .swf_parser import BitStream, SWFTag

SOUND_FORMAT_NAMES = {
    0: "PCM (native)",
    1: "ADPCM",
    2: "MP3",
    3: "PCM (little-endian)",
    4: "Nellymoser 16kHz",
    5: "Nellymoser 8kHz",
    6: "Nellymoser",
    11: "Speex",
}

SOUND_RATES = [5512, 11025, 22050, 44100]

_STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767,
]

_INDEX_TABLES = {
    2: [-1, 2],
    3: [-1, -1, 2, 4],
    4: [-1, -1, -1, -1, 2, 4, 6, 8],
    5: [-1, -1, -1, -1, -1, -1, -1, -1, 1, 2, 4, 6, 8, 10, 13, 16],
}

def parse_define_sound(tag):
    """Devuelve dict con metadata del DefineSound (tag 14) y sus datos."""
    data = tag.data
    if len(data) < 7:
        return None
    sound_id = int.from_bytes(data[0:2], "little")
    info = data[2]
    fmt = (info >> 4) & 0x0F
    rate = SOUND_RATES[(info >> 2) & 0x03]
    bits = 16 if (info >> 1) & 0x01 else 8
    channels = 2 if info & 0x01 else 1
    sample_count = int.from_bytes(data[3:7], "little")
    return {
        "sound_id": sound_id,
        "format": fmt,
        "format_name": SOUND_FORMAT_NAMES.get(fmt, f"unknown_{fmt}"),
        "rate": rate,
        "bits": bits,
        "channels": channels,
        "sample_count": sample_count,
        "data": data[7:],
    }

def _pcm_to_wav(pcm_bytes, channels, rate, sample_width):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(rate)
        w.writeframes(pcm_bytes)
    return buf.getvalue()

def decode_adpcm(data, channels):
    """Decodifica el bitstream ADPCM propio de SWF a PCM s16le intercalado."""
    stream = BitStream(data)
    code_bits = stream.read_bits(2) + 2
    index_table = _INDEX_TABLES[code_bits]
    mag_bits = code_bits - 1
    out = bytearray()

    def total_bits_left():
        return (len(stream.data) - stream.byte_offset) * 8 - stream.bit_offset

    try:
        while total_bits_left() >= channels * 22:
            samples = [0] * channels
            indexes = [0] * channels
            for ch in range(channels):
                samples[ch] = stream.read_signed_bits(16)
                indexes[ch] = min(88, stream.read_bits(6))
                out += struct.pack("<h", samples[ch])
            # 4095 codes per channel per packet, interleaved for stereo
            for _ in range(4095):
                if total_bits_left() < code_bits * channels:
                    break
                for ch in range(channels):
                    code = stream.read_bits(code_bits)
                    sign = code >> mag_bits
                    mag = code & ((1 << mag_bits) - 1)
                    step = _STEP_TABLE[indexes[ch]]
                    diff = step >> mag_bits
                    for i in range(mag_bits):
                        if mag & (1 << (mag_bits - 1 - i)):
                            diff += step >> i
                    samples[ch] = samples[ch] - diff if sign else samples[ch] + diff
                    samples[ch] = max(-32768, min(32767, samples[ch]))
                    indexes[ch] = max(0, min(88, indexes[ch] + index_table[mag]))
                    out += struct.pack("<h", samples[ch])
    except (IndexError, EOFError):
        pass
    return bytes(out)

def export_sound(tag):
    """
    Exporta un DefineSound (tag 14) a (bytes, ext) o (None, None).
    """
    snd = parse_define_sound(tag)
    if snd is None:
        return None, None
    fmt, data = snd["format"], snd["data"]

    if fmt == 2:  # MP3: 2 bytes de seek samples + frames
        return data[2:], "mp3"
    if fmt in (0, 3):  # PCM sin comprimir (8-bit unsigned / 16-bit signed LE)
        return _pcm_to_wav(data, snd["channels"], snd["rate"], snd["bits"] // 8), "wav"
    if fmt == 1:  # ADPCM
        pcm = decode_adpcm(data, snd["channels"])
        if not pcm:
            return None, None
        return _pcm_to_wav(pcm, snd["channels"], snd["rate"], 2), "wav"
    # Nellymoser/Speex: sin decoder, exportar crudo
    return data, "bin"

def export_stream_sound(tags):
    """
    Agrega los SoundStreamBlock (19) de la timeline en un único archivo.
    Solo soporta streams MP3 (el caso abrumadoramente común).
    Devuelve (bytes, ext, info) o (None, None, None).
    """
    head = None
    for tag in tags:
        if tag.tag_type in (18, 45) and len(tag.data) >= 4:  # SoundStreamHead/2
            info = tag.data[1]
            head = {
                "format": (info >> 4) & 0x0F,
                "rate": SOUND_RATES[(info >> 2) & 0x03],
                "channels": 2 if info & 0x01 else 1,
            }
            break
    if head is None or head["format"] != 2:
        return None, None, None

    frames = bytearray()
    for tag in tags:
        if tag.tag_type == 19 and len(tag.data) > 4:  # SoundStreamBlock
            # MP3STREAMSOUNDDATA: sample_count u16 + seek s16 + frames
            frames += tag.data[4:]
    if not frames:
        return None, None, None
    return bytes(frames), "mp3", head


def replace_define_sound(tag, new_sound_bytes: bytes, extension: str = "mp3") -> SWFTag:
    """Reemplaza los datos de audio de un DefineSound (tag 14) preservando su sound_id.
    Soporta archivos MP3 y WAV.
    """
    if len(tag.data) < 7:
        raise ValueError("Malformed DefineSound tag")
    sound_id = int.from_bytes(tag.data[0:2], "little")
    ext = extension.lower().lstrip(".")

    if ext == "wav":
        try:
            with wave.open(io.BytesIO(new_sound_bytes), "rb") as w:
                channels = w.getnchannels()
                rate = w.getframerate()
                sampwidth = w.getsampwidth()
                nframes = w.getnframes()
                raw_pcm = w.readframes(nframes)
        except Exception as exc:
            raise ValueError(f"Invalid WAV audio data: {exc}") from exc

        rate_diffs = [abs(rate - r) for r in SOUND_RATES]
        rate_code = rate_diffs.index(min(rate_diffs))

        fmt = 3  # PCM little-endian
        bits_code = 1 if sampwidth == 2 else 0
        channels_code = 1 if channels == 2 else 0

        flags = (fmt << 4) | (rate_code << 2) | (bits_code << 1) | channels_code
        payload = bytearray()
        payload.extend(sound_id.to_bytes(2, "little"))
        payload.append(flags)
        payload.extend(min(nframes, 0xFFFFFFFF).to_bytes(4, "little"))
        payload.extend(raw_pcm)
        return SWFTag(14, bytes(payload))

    elif ext == "mp3":
        rate_code = 3  # 44.1 kHz
        bits_code = 1  # 16-bit
        channels_code = 1  # stereo
        fmt = 2  # MP3
        flags = (fmt << 4) | (rate_code << 2) | (bits_code << 1) | channels_code

        sample_count = max(1152, (len(new_sound_bytes) * 44100) // 16000)

        payload = bytearray()
        payload.extend(sound_id.to_bytes(2, "little"))
        payload.append(flags)
        payload.extend(min(sample_count, 0xFFFFFFFF).to_bytes(4, "little"))
        payload.extend(b"\x00\x00")  # SeekSamples = 0
        payload.extend(new_sound_bytes)
        return SWFTag(14, bytes(payload))

    else:
        raise ValueError(f"Unsupported audio format for replacement: .{ext}. Use MP3 or WAV.")

