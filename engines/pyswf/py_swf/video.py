"""
Soporte de video SWF: DefineVideoStream (60) y VideoFrame (61).
Exporta metadatos y frames a formatos estándar.
"""
import io
import struct
from .swf_parser import SWFTag

VIDEO_STREAM_TAG = 60
VIDEO_FRAME_TAG = 61

VIDEO_CODEC_NAMES = {
    2: "Sorenson Spark (H.263)",
    3: "Screen Video",
    4: "On2 VP6",
    5: "On2 VP6 with Alpha",
    6: "Screen Video v2",
    7: "H.264 (AVC)",
}

def parse_video_stream(tag):
    """Parsea DefineVideoStream (tag 60)."""
    if tag.tag_type != VIDEO_STREAM_TAG or len(tag.data) < 5:
        return None
    data = tag.data
    char_id = int.from_bytes(data[0:2], "little")
    num_frames = int.from_bytes(data[2:4], "little")
    codec = data[4]
    return {
        "char_id": char_id,
        "num_frames": num_frames,
        "codec": codec,
        "codec_name": VIDEO_CODEC_NAMES.get(codec, f"Unknown ({codec})"),
    }

def parse_video_frame(tag):
    """Parsea VideoFrame (tag 61)."""
    if tag.tag_type != VIDEO_FRAME_TAG or len(tag.data) < 6:
        return None
    data = tag.data
    stream_id = int.from_bytes(data[0:2], "little")
    frame_num = int.from_bytes(data[2:4], "little")
    # data[4:6] = reserved
    video_data = data[6:]
    return {
        "stream_id": stream_id,
        "frame_num": frame_num,
        "data_size": len(video_data),
        "data": video_data,
    }

def export_video_frames(swf_tags):
    """
    Agrupa VideoFrames por stream_id y exporta como contenedores FLV/MP4 básicos.
    Devuelve lista de dicts: {stream_id, codec, frames: [(frame_num, data), ...]}
    """
    streams = {}
    for tag in swf_tags:
        if tag.tag_type == VIDEO_STREAM_TAG:
            info = parse_video_stream(tag)
            if info:
                streams[info["char_id"]] = {
                    "codec": info["codec"],
                    "codec_name": info["codec_name"],
                    "num_frames": info["num_frames"],
                    "frames": [],
                }
        elif tag.tag_type == VIDEO_FRAME_TAG:
            info = parse_video_frame(tag)
            if info and info["stream_id"] in streams:
                streams[info["stream_id"]]["frames"].append(
                    (info["frame_num"], info["data"])
                )
    
    # Ordenar frames por frame_num
    for s in streams.values():
        s["frames"].sort(key=lambda x: x[0])
    
    return list(streams.values())

def write_flv_header(codec):
    """Genera header FLV básico."""
    header = bytearray(b"FLV")
    header.append(1)  # version
    header.append(1)  # type flags: video only
    header.extend((9).to_bytes(4, "big"))  # header size
    header.extend((0).to_bytes(4, "big"))  # prev tag size 0
    return bytes(header)

def write_flv_video_tag(data, codec, timestamp, is_keyframe=True):
    """Escribe un tag FLV video."""
    # FLV video tag header
    frame_type = 1 if is_keyframe else 2  # keyframe / inter
    codec_id = codec & 0x0F
    tag_header = bytearray()
    tag_header.append((frame_type << 4) | codec_id)
    tag_header.append(0)  # AVCPacketType (0 para no H.264)
    tag_header.extend((0).to_bytes(3, "big"))  # composition time
    tag_header.extend(len(data).to_bytes(3, "big"))  # data size
    tag_header.extend(timestamp.to_bytes(3, "big"))  # timestamp
    tag_header.append((timestamp >> 24) & 0xFF)  # timestamp extended
    tag_header.extend((0).to_bytes(3, "big"))  # stream ID
    return bytes(tag_header) + data + (len(tag_header) + len(data)).to_bytes(4, "big")

def export_to_flv(streams):
    """Exporta streams de video a FLV bytes."""
    if not streams:
        return None
    # Por simplicidad, exportar solo el primer stream
    s = streams[0]
    out = bytearray()
    out.extend(write_flv_header(s["codec"]))
    for i, (frame_num, data) in enumerate(s["frames"]):
        is_key = (i == 0)
        ts = frame_num * 33  # ~30fps aproximado
        out.extend(write_flv_video_tag(data, s["codec"], ts, is_key))
    return bytes(out)