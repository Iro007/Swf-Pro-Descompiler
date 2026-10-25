import zlib
import lzma
import struct
import os

MAX_DECOMPRESSED_BYTES = int(os.environ.get("PY_SWF_MAX_DECOMPRESSED_BYTES", 256 * 1024 * 1024))

TAG_NAMES = {
    0: "End",
    1: "ShowFrame",
    2: "DefineShape",
    3: "FreeCharacter",
    4: "PlaceObject",
    5: "RemoveObject",
    6: "DefineBits",
    7: "DefineButton",
    8: "JPEGTables",
    9: "SetBackgroundColor",
    10: "DefineFont",
    11: "DefineText",
    12: "DoAction",
    13: "DefineFontInfo",
    14: "DefineSound",
    15: "StartSound",
    17: "DefineButtonSound",
    18: "SoundStreamHead",
    19: "SoundStreamBlock",
    20: "DefineBitsLossless",
    21: "DefineBitsJPEG2",
    22: "DefineShape2",
    23: "DefineButtonCxform",
    24: "Protect",
    26: "PlaceObject2",
    28: "RemoveObject2",
    32: "DefineShape3",
    33: "DefineText2",
    34: "DefineButton2",
    35: "DefineBitsJPEG3",
    36: "DefineBitsLossless2",
    37: "DefineEditText",
    38: "DefineVideo",
    39: "DefineSprite",
    40: "NameCharacter",
    41: "ProductInfo",
    42: "DefineTextFormat",
    43: "FrameLabel",
    45: "SoundStreamHead2",
    46: "DefineMorphShape",
    48: "DefineFont2",
    49: "GenCommand",
    51: "CharacterSet",
    52: "FontRef",
    56: "ExportAssets",
    57: "ImportAssets",
    58: "EnableDebugger",
    59: "DoInitAction",
    60: "DefineVideoStream",
    61: "VideoFrame",
    62: "DefineFontInfo2",
    63: "DebugID",
    64: "EnableDebugger2",
    65: "ScriptLimits",
    66: "SetTabIndex",
    69: "FileAttributes",
    70: "PlaceObject3",
    71: "ImportAssets2",
    72: "DoABC1",
    73: "DefineFontAlignZones",
    74: "CSMTextSettings",
    75: "DefineFont3",
    76: "SymbolClass",
    77: "Metadata",
    78: "DefineScalingGrid",
    82: "DoABC",
    83: "DefineShape4",
    84: "DefineMorphShape2",
    86: "DefineSceneAndFrameLabelData",
    87: "DefineBinaryData",
    88: "DefineFontName",
    89: "StartSound2",
    90: "DefineBitsJPEG4",
    91: "DefineFont4",
    93: "EnableTelemetry",
    94: "PlaceObject4",
}

def collect_symbol_names(tags):
    """
    Mapea character id -> nombre a partir de SymbolClass (76) y ExportAssets (56).
    Ambos comparten formato: UI16 count, luego (UI16 char_id, STRING nombre).
    """
    names = {}
    for tag in tags:
        if tag.tag_type not in (56, 76) or len(tag.data) < 2:
            continue
        count = int.from_bytes(tag.data[0:2], "little")
        pos = 2
        for _ in range(count):
            if pos + 2 > len(tag.data):
                break
            char_id = int.from_bytes(tag.data[pos : pos + 2], "little")
            pos += 2
            end = tag.data.find(b"\x00", pos)
            if end == -1:
                break
            name = tag.data[pos:end].decode("utf-8", errors="replace")
            pos = end + 1
            names.setdefault(char_id, name)
    return names

# Definition tags whose payload starts with a UI16 character id
CHARACTER_TAGS = {
    2, 3, 6, 7, 10, 11, 13, 14, 20, 21, 22, 32, 33, 34, 35, 36, 37, 38,
    39, 40, 46, 48, 51, 52, 60, 75, 83, 84, 87, 90, 91,
}

class BitStream:
    def __init__(self, data):
        self.data = data
        self.byte_offset = 0
        self.bit_offset = 0
        
    def read_bits(self, nbits):
        if nbits == 0:
            return 0
        val = 0
        bits_needed = nbits
        while bits_needed > 0:
            if self.byte_offset >= len(self.data):
                raise EOFError("BitStream EOF")
            current_byte = self.data[self.byte_offset]
            bits_available = 8 - self.bit_offset
            bits_to_read = min(bits_needed, bits_available)
            shift = bits_available - bits_to_read
            mask = (1 << bits_to_read) - 1
            chunk = (current_byte >> shift) & mask
            val = (val << bits_to_read) | chunk
            
            self.bit_offset += bits_to_read
            if self.bit_offset == 8:
                self.bit_offset = 0
                self.byte_offset += 1
            bits_needed -= bits_to_read
        return val

    def read_signed_bits(self, nbits):
        val = self.read_bits(nbits)
        if nbits > 0 and (val & (1 << (nbits - 1))):
            val -= (1 << nbits)
        return val
        
    def align(self):
        if self.bit_offset > 0:
            self.bit_offset = 0
            self.byte_offset += 1

    def read_u16_le(self):
        """Byte-aligned little-endian UI16 (SWF multi-byte ints are LE)."""
        lo = self.read_bits(8)
        hi = self.read_bits(8)
        return lo | (hi << 8)

    def read_s16_le(self):
        val = self.read_u16_le()
        return val - 0x10000 if val & 0x8000 else val

def parse_rect(stream):
    nbits = stream.read_bits(5)
    xmin = stream.read_signed_bits(nbits)
    xmax = stream.read_signed_bits(nbits)
    ymin = stream.read_signed_bits(nbits)
    ymax = stream.read_signed_bits(nbits)
    stream.align()
    return {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}

def make_rect(xmin, xmax, ymin, ymax):
    # Find the max bits needed to represent any of these coords (signed)
    max_val = max(abs(xmin), abs(xmax), abs(ymin), abs(ymax))
    nbits = 0
    # Calculate bits needed for the value (excluding sign bit)
    while (1 << nbits) <= max_val:
        nbits += 1
    # Plus sign bit
    nbits += 1
    # Minimum bit size is 1 bit (if all are 0)
    nbits = max(1, nbits)
    
    # We pack: nbits (5 bits) + 4 coordinates * nbits
    total_bits = 5 + 4 * nbits
    total_bytes = (total_bits + 7) // 8
    data = bytearray(total_bytes)
    
    bit_offset = 0
    def write_bits(val, num_bits):
        nonlocal bit_offset
        val = val & ((1 << num_bits) - 1)
        for i in range(num_bits):
            bit_idx = num_bits - 1 - i
            bit = (val >> bit_idx) & 1
            byte_idx = bit_offset // 8
            shift = 7 - (bit_offset % 8)
            data[byte_idx] |= (bit << shift)
            bit_offset += 1
            
    write_bits(nbits, 5)
    write_bits(xmin, nbits)
    write_bits(xmax, nbits)
    write_bits(ymin, nbits)
    write_bits(ymax, nbits)
    return bytes(data)

class SWFTag:
    def __init__(self, tag_type, data, parse_error=None):
        self.tag_type = tag_type
        self.data = data
        self.parse_error = parse_error

    @property
    def name(self):
        return TAG_NAMES.get(self.tag_type, f"Unknown_{self.tag_type}")
        
    @property
    def is_doabc(self):
        return self.tag_type in (72, 82)

    @property
    def char_id(self):
        if self.tag_type in CHARACTER_TAGS and len(self.data) >= 2:
            return int.from_bytes(self.data[0:2], "little")
        return None
        
    def parse_doabc(self):
        if self.tag_type == 72:
            return (0, "", self.data) if self.data else None
        if not self.is_doabc or len(self.data) < 4:
            return None
        flags = int.from_bytes(self.data[0:4], "little")
        name = ""
        abc_bytes = b""
        if len(self.data) > 4:
            name_end = self.data.find(b"\x00", 4)
            if name_end == -1:
                name = self.data[4:].decode("utf-8", errors="replace")
                abc_bytes = self.data[4:]
            else:
                name = self.data[4:name_end].decode("utf-8", errors="replace")
                abc_bytes = self.data[name_end + 1:]
        return flags, name, abc_bytes
        
    def pack(self):
        tag_len = len(self.data)
        if tag_len < 63:
            header = (self.tag_type << 6) | tag_len
            return header.to_bytes(2, "little") + self.data
        else:
            header = (self.tag_type << 6) | 0x3F
            return header.to_bytes(2, "little") + tag_len.to_bytes(4, "little") + self.data

    def __repr__(self):
        return f"<SWFTag {self.name} ({self.tag_type}), size={len(self.data)}>"

def decompress_zws(rest_data, decompressed_length, *, strict=False):
    """Decompress the body of a ZWS (LZMA) SWF.

    Layout after the 8-byte header: 4 bytes compressed length, 5 bytes LZMA
    properties, then the raw stream. We rebuild a standard LZMA-alone header
    (properties + u64 size). SWF streams normally carry an end-of-payload
    marker, which liblzma rejects when the size is declared, so the size is
    first left unknown; if that fails we retry with the size declared
    (the SWF length field includes the 8 header bytes, so subtract them).
    """
    if len(rest_data) < 9:
        raise ValueError("ZWS data too short")
    props = rest_data[4:9]
    payload = rest_data[9:]
    unknown = (2 ** 64 - 1).to_bytes(8, "little")
    if decompressed_length > MAX_DECOMPRESSED_BYTES:
        raise ValueError("SWF decompressed size exceeds safety limit")
    if strict and int.from_bytes(rest_data[:4], "little") != len(payload):
        raise ValueError("ZWS compressed length does not match payload")
    def decode(size):
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=MAX_DECOMPRESSED_BYTES)
        result = decoder.decompress(props + size + payload, max_length=MAX_DECOMPRESSED_BYTES + 1)
        if len(result) > MAX_DECOMPRESSED_BYTES or not decoder.eof and not decoder.needs_input:
            raise ValueError("LZMA decompressed size exceeded safety limit")
        if strict and (not decoder.eof or decoder.unused_data):
            raise ValueError("Incomplete LZMA stream or trailing compressed data")
        return result
    try:
        return decode(unknown)
    except lzma.LZMAError:
        try:
            size = max(decompressed_length - 8, 0).to_bytes(8, "little")
            return decode(size)
        except lzma.LZMAError as err:
            raise ValueError(f"Corrupted LZMA stream: {err}") from err


def decompress_body(signature, rest_data, decompressed_length, *, strict=False):
    """Bound allocation before decoding, including tolerant inspection paths."""
    if decompressed_length > MAX_DECOMPRESSED_BYTES:
        raise ValueError("SWF decompressed size exceeds safety limit")
    if signature == "CWS":
        decoder = zlib.decompressobj()
        try:
            result = decoder.decompress(rest_data, MAX_DECOMPRESSED_BYTES + 1)
        except zlib.error as err:
            raise ValueError(f"Corrupted zlib stream: {err}") from err
        if len(result) > MAX_DECOMPRESSED_BYTES or decoder.unconsumed_tail:
            raise ValueError("zlib decompressed size exceeded safety limit")
        if strict and (not decoder.eof or decoder.unused_data):
            raise ValueError("Incomplete zlib stream or trailing compressed data")
    elif signature == "ZWS":
        result = decompress_zws(rest_data, decompressed_length, strict=strict)
    else:
        if len(rest_data) + 8 > MAX_DECOMPRESSED_BYTES:
            raise ValueError("FWS size exceeds safety limit")
        result = rest_data
    if strict and len(result) + 8 != decompressed_length:
        raise ValueError("SWF header length does not match decompressed size")
    return result


class SWFFile:
    def __init__(self):
        self.signature = "FWS"
        self.version = 15
        self.rect = {"xmin": 0, "xmax": 11000, "ymin": 0, "ymax": 8250}
        self.frame_rate = 12.0
        self.frame_count = 1
        self.tags = []

    def read_file(self, filepath):
        with open(filepath, "rb") as f:
            self.read_bytes(f.read())

    def read_bytes(self, file_bytes, *, strict=False):
        header_bytes = file_bytes[:8]
        if len(header_bytes) < 8:
            raise ValueError("Invalid SWF file: header too short")

        self.signature = header_bytes[0:3].decode("ascii", errors="ignore")
        if self.signature not in ("FWS", "CWS", "ZWS"):
            raise ValueError(f"Unknown SWF signature: {self.signature}")

        self.version = header_bytes[3]
        decompressed_length = int.from_bytes(header_bytes[4:8], "little")
        if decompressed_length > MAX_DECOMPRESSED_BYTES:
            raise ValueError(f"SWF decompressed size ({decompressed_length} bytes) exceeds limit of {MAX_DECOMPRESSED_BYTES} bytes")

        rest_data = file_bytes[8:]

        decompressed_data = decompress_body(self.signature, rest_data, decompressed_length, strict=strict)
        try:
            self._parse_decompressed(decompressed_data, strict=strict)
        except (IndexError, struct.error) as err:
            raise ValueError(f"Corrupted or truncated SWF data: {err}") from err

    def _parse_decompressed(self, data, *, strict=False):
        if not data:
            raise ValueError("Truncated SWF: empty decompressed stream")
        stream = BitStream(data)
        try:
            self.rect = parse_rect(stream)
        except (EOFError, IndexError) as err:
            raise ValueError(f"Truncated SWF rect header: {err}") from err
        
        # Read FrameRate
        # Minor byte (fraction), Major byte (integer)
        # We read 2 bytes from stream
        if stream.byte_offset + 2 > len(data):
            raise ValueError("Truncated SWF header: missing frame rate")
        fps_bytes = data[stream.byte_offset : stream.byte_offset + 2]
        stream.byte_offset += 2
        
        fraction = fps_bytes[0]
        integer = fps_bytes[1]
        self.frame_rate = integer + fraction / 256.0
        
        # Read FrameCount (UI16)
        if stream.byte_offset + 2 > len(data):
            raise ValueError("Truncated SWF header: missing frame count")
        self.frame_count = int.from_bytes(data[stream.byte_offset : stream.byte_offset + 2], "little")
        stream.byte_offset += 2
        
        # Parse tags
        self.tags = []
        offset = stream.byte_offset
        while offset < len(data):
            if offset + 2 > len(data):
                break
            header = int.from_bytes(data[offset : offset + 2], "little")
            tag_type = header >> 6
            tag_len = header & 0x3F
            length_bytes = 2
            if tag_len == 0x3F:
                if offset + 6 > len(data):
                    break
                tag_len = int.from_bytes(data[offset + 2 : offset + 6], "little")
                length_bytes = 6
                
            offset += length_bytes
            tag_data = data[offset : offset + tag_len]
            parse_error = None
            if len(tag_data) < tag_len:
                parse_error = f"truncated tag: expected {tag_len} bytes, got {len(tag_data)}"
            offset += tag_len

            tag = SWFTag(tag_type, tag_data, parse_error=parse_error)
            self.tags.append(tag)

            if tag_type == 0:  # End Tag
                if strict and offset != len(data):
                    raise ValueError("Data after SWF End tag")
                break

    def save_file(self, filepath):
        with open(filepath, "wb") as f:
            f.write(self.save_bytes())

    def save_bytes(self):
        # 1. Rebuild decompressed body
        body = bytearray()
        rect_bytes = make_rect(self.rect["xmin"], self.rect["xmax"], self.rect["ymin"], self.rect["ymax"])
        body.extend(rect_bytes)
        
        # FrameRate: minor byte (fraction), major byte (integer)
        integer = int(self.frame_rate)
        fraction = int((self.frame_rate - integer) * 256) & 0xFF
        body.append(fraction)
        body.append(integer)
        
        # FrameCount
        body.extend(self.frame_count.to_bytes(2, "little"))
        
        # Pack all tags
        # Ensure there is an End tag at the end
        if not self.tags or self.tags[-1].tag_type != 0:
            self.tags.append(SWFTag(0, b""))
            
        for tag in self.tags:
            body.extend(tag.pack())
            
        # Total decompressed file size = body size + 8 bytes header
        decompressed_size = len(body) + 8
        
        header = bytearray()
        header.extend(self.signature.encode("ascii"))
        header.append(self.version)
        header.extend(decompressed_size.to_bytes(4, "little"))
        
        if self.signature == "CWS":
            compressed_body = zlib.compress(body)
        elif self.signature == "ZWS":
            # LZMA compression:
            # We compress the body using lzma, then extract properties.
            # Python's lzma.compress yields a stream with properties + uncompressed size (8 bytes) + payload
            # We need to construct: 4 bytes compressed len, 5 bytes properties, then payload
            raw_lzma = lzma.compress(body, format=lzma.FORMAT_ALONE)
            properties = raw_lzma[0:5]
            # uncompressed size raw_lzma[5:13] is omitted in SWF
            payload = raw_lzma[13:]
            compressed_len = len(payload)
            compressed_body = bytearray()
            compressed_body.extend(compressed_len.to_bytes(4, "little"))
            compressed_body.extend(properties)
            compressed_body.extend(payload)
        else:
            compressed_body = body

        return bytes(header) + bytes(compressed_body)
