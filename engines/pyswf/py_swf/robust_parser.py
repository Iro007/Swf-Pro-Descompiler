"""
Parsing robusto de SWF con recuperación de errores.
Proporciona modo estricto y modo tolerante para archivos corruptos.
"""
import zlib
import lzma
import struct
from .swf_parser import (
    BitStream, SWFTag, SWFFile, TAG_NAMES, CHARACTER_TAGS,
    parse_rect, make_rect, collect_symbol_names, decompress_zws, decompress_body,
)


class SWFParseError(Exception):
    """Error durante el parsing de SWF."""
    pass


class CorruptSWFError(SWFParseError):
    """Error específico de archivo corrupto."""
    pass


def read_swf_robust(filepath, tolerant=True, max_tag_errors=100):
    """
    Lee un SWF con recuperación de errores.
    
    Args:
        filepath: ruta al archivo
        tolerant: si True, intenta recuperar tags corruptos en lugar de fallar
        max_tag_errors: máximo número de errores de tag antes de abortar
    
    Returns:
        SWFFile con tags parseados (algunos pueden tener parse_error)
    """
    with open(filepath, "rb") as f:
        return read_swf_bytes_robust(f.read(), tolerant, max_tag_errors)


def read_swf_bytes_robust(file_bytes, tolerant=True, max_tag_errors=100):
    """Versión que acepta bytes directamente."""
    swf = SWFFile()
    
    if len(file_bytes) < 8:
        raise CorruptSWFError("File too short for SWF header")
    
    header_bytes = file_bytes[:8]
    signature = header_bytes[0:3].decode("ascii", errors="ignore")
    
    if signature not in ("FWS", "CWS", "ZWS"):
        if tolerant:
            # Intentar detectar firma en otra posición
            for i in range(min(16, len(file_bytes) - 3)):
                sig = file_bytes[i:i+3].decode("ascii", errors="ignore")
                if sig in ("FWS", "CWS", "ZWS"):
                    signature = sig
                    file_bytes = file_bytes[i:]
                    header_bytes = file_bytes[:8]
                    break
            else:
                raise CorruptSWFError(f"Unknown SWF signature: {header_bytes[:3]!r}")
        else:
            raise CorruptSWFError(f"Unknown SWF signature: {signature}")
    
    swf.signature = signature
    swf.version = header_bytes[3]
    decompressed_length = int.from_bytes(header_bytes[4:8], "little")
    
    rest_data = file_bytes[8:]
    
    # Descomprimir
    try:
        decompressed_data = decompress_body(signature, rest_data, decompressed_length, strict=not tolerant)
    except Exception as e:
        if tolerant and isinstance(e, (zlib.error, lzma.LZMAError)):
            # Intentar descompresión parcial
            decompressed_data = _try_partial_decompress(signature, rest_data, decompressed_length)
            if decompressed_data is None:
                raise CorruptSWFError(f"Decompression failed: {e}")
        else:
            raise CorruptSWFError(f"Decompression failed: {e}")
    
    # Parsear body descomprimido con recuperación
    swf._parse_decompressed_robust(decompressed_data, tolerant, max_tag_errors)
    
    return swf


def _try_partial_decompress(signature, data, expected_len):
    """Intenta descomprimir parcialmente datos corruptos."""
    if signature == "CWS":
        try:
            # zlib puede tolerar basura al final
            return decompress_body(signature, data, expected_len)
        except Exception:
            # Intentar descompresión incremental
            for i in range(len(data), max(0, len(data) - 65536), -1024):
                try:
                    return decompress_body(signature, data[:i], expected_len)
                except Exception:
                    continue
    elif signature == "ZWS":
        try:
            return decompress_zws(data, expected_len)
        except Exception:
            pass
    return None


def _parse_decompressed_robust(self, data, tolerant=True, max_tag_errors=100):
    """Parsea el body descomprimido con recuperación de errores."""
    stream = BitStream(data)
    
    try:
        self.rect = parse_rect(stream)
    except Exception as e:
        if tolerant:
            # Rect por defecto
            self.rect = {"xmin": 0, "xmax": 11000, "ymin": 0, "ymax": 8250}
            # Avanzar stream manualmente
            stream.byte_offset = min(20, len(data))
        else:
            raise CorruptSWFError(f"Failed to parse RECT: {e}")
    
    # FrameRate
    try:
        fps_bytes = data[stream.byte_offset : stream.byte_offset + 2]
        if len(fps_bytes) >= 2:
            fraction, integer = fps_bytes[0], fps_bytes[1]
            self.frame_rate = integer + fraction / 256.0
        else:
            self.frame_rate = 12.0
        stream.byte_offset += 2
    except Exception:
        self.frame_rate = 12.0
    
    # FrameCount
    try:
        if stream.byte_offset + 2 <= len(data):
            self.frame_count = int.from_bytes(data[stream.byte_offset : stream.byte_offset + 2], "little")
            stream.byte_offset += 2
        else:
            self.frame_count = 1
    except Exception:
        self.frame_count = 1
    
    # Parsear tags con recuperación
    self.tags = []
    offset = stream.byte_offset
    error_count = 0
    max_iterations = 10000  # Prevenir bucles infinitos
    
    while offset < len(data) and error_count < max_tag_errors and max_iterations > 0:
        max_iterations -= 1
        
        if offset + 2 > len(data):
            # Datos truncados al final
            if tolerant:
                break
            else:
                raise CorruptSWFError("Truncated tag header at end of file")
        
        try:
            header = int.from_bytes(data[offset : offset + 2], "little")
            tag_type = header >> 6
            tag_len = header & 0x3F
            length_bytes = 2
            
            if tag_len == 0x3F:
                if offset + 6 > len(data):
                    if tolerant:
                        break
                    raise CorruptSWFError("Long tag header truncated")
                tag_len = int.from_bytes(data[offset + 2 : offset + 6], "little")
                length_bytes = 6
            
            offset += length_bytes
            
            # Validar longitud razonable
            if tag_len > len(data) * 2:  # Sanidad: tag no puede ser 2x el archivo
                if tolerant:
                    error_count += 1
                    # Saltar tag corrupto
                    offset += min(tag_len, len(data) - offset)
                    continue
                else:
                    raise CorruptSWFError(f"Tag {tag_type} has unreasonable length: {tag_len}")
            
            if offset + tag_len > len(data):
                # Tag truncado
                if tolerant:
                    tag_data = data[offset:]
                    parse_error = f"truncated: expected {tag_len} bytes, got {len(tag_data)}"
                    error_count += 1
                else:
                    raise CorruptSWFError(f"Tag {tag_type} truncated at offset {offset}")
            else:
                tag_data = data[offset : offset + tag_len]
                parse_error = None
            
            offset += tag_len
            
            tag = SWFTag(tag_type, tag_data, parse_error=parse_error)
            self.tags.append(tag)
            
            if tag_type == 0:  # End Tag
                break
                
        except Exception as e:
            if tolerant:
                error_count += 1
                # Intentar sincronizar al siguiente tag posible
                offset = _resync_to_next_tag(data, offset)
                if offset == -1:
                    break
            else:
                raise CorruptSWFError(f"Failed to parse tag at offset {offset}: {e}")
    
    # Asegurar End tag
    if tolerant and (not self.tags or self.tags[-1].tag_type != 0):
        self.tags.append(SWFTag(0, b""))
    
    if error_count > 0:
        print(f"SWF parse completed with {error_count} tag errors (tolerant mode)")


def _resync_to_next_tag(data, offset):
    """Intenta encontrar el siguiente tag válido escaneando hacia adelante."""
    # Buscar patrón de tag header (2 bytes) que parezca válido
    for i in range(offset + 1, min(offset + 1024, len(data) - 1)):
        # Verificar si podría ser un tag header válido
        header = int.from_bytes(data[i:i+2], "little")
        tag_type = header >> 6
        tag_len = header & 0x3F
        
        # Tag types válidos conocidos
        if tag_type in TAG_NAMES or tag_type == 0:
            # Verificar que la longitud es razonable
            if tag_len != 0x3F:
                if i + 2 + tag_len <= len(data):
                    return i
            else:
                if i + 6 <= len(data):
                    long_len = int.from_bytes(data[i+2:i+6], "little")
                    if long_len < len(data) * 2:
                        return i
    return -1


# Monkey-patch para SWFFile
SWFFile._parse_decompressed_robust = _parse_decompressed_robust


def validate_swf(filepath):
    """
    Valida un SWF y reporta problemas.
    Returns: (is_valid, errors, warnings)
    """
    from .validation import validate_swf_bytes
    from .swf_parser import MAX_DECOMPRESSED_BYTES
    try:
        if hasattr(filepath, "read"):
            data = filepath.read(MAX_DECOMPRESSED_BYTES + 1)
        else:
            with open(filepath, "rb") as stream:
                data = stream.read(MAX_DECOMPRESSED_BYTES + 1)
        if len(data) > MAX_DECOMPRESSED_BYTES:
            raise ValueError("SWF input exceeds the size limit")
        result = validate_swf_bytes(data)
        return result["valid"], result["errors"], result["warnings"]
    except Exception as error:
        return False, [str(error)], []


def repair_swf(input_path, output_path):
    """
    Intenta reparar un SWF corrupto leyéndolo en modo tolerante y re-escribiéndolo.
    """
    try:
        swf = read_swf_robust(input_path, tolerant=True)
        from .storage import write_swf
        write_swf(output_path, swf.save_bytes(), original=input_path)
        return True, "Repaired successfully"
    except Exception as e:
        return False, f"Repair failed: {e}"
