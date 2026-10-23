"""
Exportación de paquetes de recursos JSON y sprite sheets.
Este módulo NO genera documentos FLA ni XFL compatibles con Adobe Animate.
"""
import io
import json
import zipfile
import base64
from pathlib import PurePosixPath
from . import shapes, morph, resources, text_fonts, timeline, sounds, video
from .swf_parser import SWFTag, collect_symbol_names


RESOURCE_BUNDLE_VERSION = "1.0"


def export_resource_bundle(swf, output_path=None):
    """
    Exporta recursos y desensamblado con un manifiesto JSON (no FLA/XFL).
    Estructura:
    /manifest.json
    /library/ (recursos: images, shapes, fonts, sounds, symbols)
    /timeline/ (frames y layers)
    /scripts/ (P-code, no código fuente reconstruido)
    """
    buf = io.BytesIO()
    
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Manifiesto del proyecto
        manifest = {
            "format": "pyswf-resource-bundle",
            "version": RESOURCE_BUNDLE_VERSION,
            "animate_compatible": False,
            "diagnostics": [],
            "stage": {
                "width": round((swf.rect["xmax"] - swf.rect["xmin"]) / 20.0),
                "height": round((swf.rect["ymax"] - swf.rect["ymin"]) / 20.0),
                "frameRate": swf.frame_rate,
                "backgroundColor": get_background_color(swf.tags),
            },
            "frameCount": swf.frame_count,
            "symbols": [],
            "resources": {
                "images": [],
                "shapes": [],
                "fonts": [],
                "sounds": [],
                "videos": [],
            },
            "timeline": [],
        }
        
        symbol_names = collect_symbol_names(swf.tags)
        jpeg_tables = resources.find_jpeg_tables(swf.tags)
        bitmap_resolver = make_bitmap_resolver(swf.tags)
        
        # Build timeline
        frames = timeline.build_timeline(swf.tags)
        for fi, frame in enumerate(frames):
            frame_data = {"frame": fi, "layers": []}
            for depth in sorted(frame.keys()):
                entry = frame[depth]
                layer = {
                    "depth": depth,
                    "char_id": entry.get("char_id"),
                    "matrix": entry.get("matrix"),
                    "ratio": entry.get("ratio", 0),
                }
                frame_data["layers"].append(layer)
            manifest["timeline"].append(frame_data)
        
        # 2. Procesar tags y exportar recursos
        char_id_to_symbol = {}
        
        for i, tag in enumerate(swf.tags):
            try:
                # Images
                if tag.tag_type in (6, 20, 21, 35, 36, 90):
                    data, ext = resources.export_image(tag, jpeg_tables=jpeg_tables)
                    if not data:
                        raise ValueError("Image decoder returned no data")
                    from PIL import Image
                    with Image.open(io.BytesIO(data)) as image:
                        image.verify()
                    if data:
                        name = f"image_{tag.char_id or i}.{ext}"
                        zf.writestr(f"library/images/{name}", data)
                        manifest["resources"]["images"].append({
                            "tag_index": i,
                            "char_id": tag.char_id,
                            "name": name,
                            "symbol_name": symbol_names.get(tag.char_id),
                            "width": None,
                            "height": None,
                            "format": ext,
                        })
                        # Try to get dimensions
                        try:
                            from PIL import Image
                            img = Image.open(io.BytesIO(data))
                            manifest["resources"]["images"][-1]["width"] = img.width
                            manifest["resources"]["images"][-1]["height"] = img.height
                        except Exception:
                            pass
                
                # Shapes
                elif tag.tag_type in (2, 22, 32, 83):
                    svg = shapes.shape_to_svg(tag, bitmap_resolver=bitmap_resolver)
                    name = f"shape_{tag.char_id or i}.svg"
                    zf.writestr(f"library/shapes/{name}", svg.encode("utf-8"))
                    manifest["resources"]["shapes"].append({
                        "tag_index": i,
                        "char_id": tag.char_id,
                        "name": name,
                        "symbol_name": symbol_names.get(tag.char_id),
                        "type": "shape",
                    })
                
                # Morph shapes
                elif tag.tag_type in (46, 84):
                    svg = morph.morph_to_svg(tag, ratio=0.0)
                    name = f"morph_{tag.char_id or i}.svg"
                    zf.writestr(f"library/shapes/{name}", svg.encode("utf-8"))
                    manifest["resources"]["shapes"].append({
                        "tag_index": i,
                        "char_id": tag.char_id,
                        "name": name,
                        "symbol_name": symbol_names.get(tag.char_id),
                        "type": "morph",
                    })
                
                # Fonts
                elif tag.tag_type in (48, 75):
                    font = text_fonts.parse_font(tag)
                    if font:
                        svg = text_fonts.font_to_svg(font)
                        name = f"font_{tag.char_id or i}.svg"
                        zf.writestr(f"library/fonts/{name}", svg.encode("utf-8"))
                        manifest["resources"]["fonts"].append({
                            "tag_index": i,
                            "char_id": tag.char_id,
                            "name": name,
                            "font_name": font["name"],
                            "num_glyphs": font["num_glyphs"],
                        })
                
                # Sounds
                elif tag.tag_type == 14:
                    data, ext = sounds.export_sound(tag)
                    if not data:
                        raise ValueError("Sound codec is unsupported or returned no data")
                    if data:
                        name = f"sound_{tag.char_id or i}.{ext}"
                        zf.writestr(f"library/sounds/{name}", data)
                        manifest["resources"]["sounds"].append({
                            "tag_index": i,
                            "char_id": tag.char_id,
                            "name": name,
                            "symbol_name": symbol_names.get(tag.char_id),
                            "format": ext,
                        })
                
                # Video
                elif tag.tag_type == 60:
                    info = video.parse_video_stream(tag)
                    if info:
                        name = f"video_{tag.char_id or i}.json"
                        zf.writestr(f"library/videos/{name}", json.dumps(info, indent=2))
                        manifest["resources"]["videos"].append({
                            "tag_index": i,
                            "char_id": tag.char_id,
                            "name": name,
                            "codec": info.get("codec_name"),
                            "num_frames": info.get("num_frames"),
                        })
                
                # Scripts (DoABC)
                elif tag.tag_type == 82:
                    parsed = tag.parse_doabc()
                    if parsed:
                        _, name, abc_bytes = parsed
                        # Export as disassembly
                        from .avm2 import ABCFile, disassemble_instructions, build_method_mapping
                        abc = ABCFile()
                        abc.parse(abc_bytes)
                        mapping = build_method_mapping(abc)
                        all_code = []
                        for j, mb in enumerate(abc.method_bodies):
                            method_name = mapping.get(mb.method, f"method_{mb.method}")
                            code = disassemble_instructions(abc.constant_pool, mb.code, exact=True, exceptions=mb.exceptions)
                            all_code.append(f"// {method_name}\n{code}")
                        script_name = f"script_{i}.pcode"
                        zf.writestr(f"scripts/{script_name}", "\n\n".join(all_code))
                
                # Sprites (MovieClips)
                elif tag.tag_type == 39:
                    parsed = timeline.parse_sprite(tag)
                    if parsed:
                        char_id, declared_frames, inner = parsed
                        symbol_name = symbol_names.get(char_id, f"Sprite_{char_id}")
                        # Export sprite timeline
                        sprite_frames = timeline.build_timeline(inner)
                        sprite_data = {
                            "char_id": char_id,
                            "name": symbol_name,
                            "declared_frames": declared_frames,
                            "actual_frames": len(sprite_frames),
                            "timeline": [],
                        }
                        for fi, frame in enumerate(sprite_frames):
                            frame_data = {"frame": fi, "layers": []}
                            for depth in sorted(frame.keys()):
                                entry = frame[depth]
                                frame_data["layers"].append({
                                    "depth": depth,
                                    "char_id": entry.get("char_id"),
                                    "matrix": entry.get("matrix"),
                                    "ratio": entry.get("ratio", 0),
                                })
                            sprite_data["timeline"].append(frame_data)
                        
                        zf.writestr(f"library/symbols/sprite_{char_id}.json", json.dumps(sprite_data, indent=2))
                        manifest["symbols"].append({
                            "char_id": char_id,
                            "name": symbol_name,
                            "type": "sprite",
                            "file": f"symbols/sprite_{char_id}.json",
                        })
                        char_id_to_symbol[char_id] = symbol_name
                
                # DefineButton / DefineButton2
                elif tag.tag_type in (7, 34):
                    char_id = tag.char_id
                    symbol_name = symbol_names.get(char_id, f"Button_{char_id}")
                    manifest["symbols"].append({
                        "char_id": char_id,
                        "name": symbol_name,
                        "type": "button",
                    })
            
            except Exception as e:
                manifest["diagnostics"].append({"tag_index":i,"tag_type":tag.tag_type,"error":str(e)})
        
        # Write manifest
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("README.txt", "PySWF resource bundle: JSON + extracted resources + P-code.\nNot a FLA/XFL document; cannot be opened as an Animate project.\nSee manifest.json diagnostics for extraction failures.\n")
    
    buf.seek(0)
    if output_path:
        with open(output_path, "wb") as f:
            f.write(buf.getvalue())
    
    return buf.getvalue()


def export_fla_project(swf, output_path=None):
    """Deprecated compatibility alias. Its bytes are a resource bundle, NOT FLA."""
    import warnings
    warnings.warn("export_fla_project does not generate FLA; use export_resource_bundle",DeprecationWarning,stacklevel=2)
    return export_resource_bundle(swf,output_path)


def export_sprite_sheet(swf, tag_indices=None, cols=8, padding=2, background=None):
    """
    Genera un sprite sheet (atlas) con todos los shapes/imágenes.
    Devuelve (png_bytes, metadata_json).
    """
    from PIL import Image
    
    if tag_indices is None:
        # Export all shapes and images
        tag_indices = [
            i for i, tag in enumerate(swf.tags)
            if tag.tag_type in (2, 22, 32, 83, 6, 20, 21, 35, 36, 90, 46, 84)
        ]
    
    jpeg_tables = resources.find_jpeg_tables(swf.tags)
    bitmap_resolver = make_bitmap_resolver(swf.tags)
    
    images = []
    metadata = {"frames": []}
    
    for idx in tag_indices:
        tag = swf.tags[idx]
        try:
            img = None
            if tag.tag_type in (6, 20, 21, 35, 36, 90):
                data, ext = resources.export_image(tag, jpeg_tables=jpeg_tables)
                if data:
                    img = Image.open(io.BytesIO(data)).convert("RGBA")
            elif tag.tag_type in (2, 22, 32, 83):
                svg = shapes.shape_to_svg(tag, bitmap_resolver=bitmap_resolver, background=background)
                # Rasterize SVG using cairosvg if available, otherwise skip
                try:
                    import cairosvg
                    png_data = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
                    img = Image.open(io.BytesIO(png_data)).convert("RGBA")
                except ImportError:
                    pass
            elif tag.tag_type in (46, 84):
                svg = morph.morph_to_svg(tag, ratio=0.0, background=background)
                try:
                    import cairosvg
                    png_data = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
                    img = Image.open(io.BytesIO(png_data)).convert("RGBA")
                except ImportError:
                    pass
            
            if img:
                images.append((img, tag.char_id, idx, tag.name))
        except Exception:
            pass
    
    if not images:
        return None, None
    
    # Calculate grid
    max_w = max(img.width for img, _, _, _ in images)
    max_h = max(img.height for img, _, _, _ in images)
    cell_w = max_w + padding
    cell_h = max_h + padding
    rows = (len(images) + cols - 1) // cols
    
    sheet_w = cols * cell_w
    sheet_h = rows * cell_h
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    
    for i, (img, char_id, tag_idx, name) in enumerate(images):
        col = i % cols
        row = i // cols
        x = col * cell_w + (cell_w - img.width) // 2
        y = row * cell_h + (cell_h - img.height) // 2
        sheet.paste(img, (x, y), img)
        
        metadata["frames"].append({
            "index": i,
            "tag_index": tag_idx,
            "char_id": char_id,
            "name": name,
            "x": x,
            "y": y,
            "width": img.width,
            "height": img.height,
        })
    
    # Save to bytes
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    png_bytes = buf.getvalue()
    
    meta_json = json.dumps(metadata, indent=2)
    
    return png_bytes, meta_json


def make_bitmap_resolver(swf_tags):
    """Resolver de bitmap fills para shapes."""
    import io
    from PIL import Image
    
    jpeg_tables = resources.find_jpeg_tables(swf_tags)
    by_id = {t.char_id: t for t in swf_tags if t.tag_type in (6, 20, 21, 35, 36, 90) and t.char_id is not None}
    
    def resolve(char_id):
        tag = by_id.get(char_id)
        if tag is None:
            return None
        data, ext = resources.export_image(tag, jpeg_tables=jpeg_tables)
        if data is None:
            return None
        try:
            img = Image.open(io.BytesIO(data))
            if ext != "png":
                buf = io.BytesIO()
                img.save(buf, "PNG")
                data = buf.getvalue()
            return data, img.width, img.height
        except Exception:
            return None
    
    return resolve


def get_background_color(tags):
    for tag in tags:
        if tag.tag_type == 9 and len(tag.data) >= 3:
            r, g, b = tag.data[0], tag.data[1], tag.data[2]
            return f"#{r:02x}{g:02x}{b:02x}"
    return None
