use crate::container::Bits;
use crate::{Asset, Diagnostic, Movie, TagRecord};
use anyhow::{Context, Result, bail, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use image::{ImageEncoder, ImageReader, RgbaImage};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, HashMap},
    fmt::Write as _,
    io::{Cursor, Read},
};
use swf::{BitmapFormat, Color, FillStyle, ShapeRecord, Tag};

const MAX_PIXELS: usize = 16 * 1024 * 1024;
const MAX_ASSET_BYTES: usize = 128 * 1024 * 1024;

fn asset(bytes: Vec<u8>, mime: &str, extension: &str, diagnostics: Vec<Diagnostic>) -> Asset {
    Asset {
        bytes,
        mime: mime.into(),
        extension: extension.into(),
        diagnostics,
    }
}
pub fn supported_formats(movie: &Movie, tag: &TagRecord) -> Vec<String> {
    let mut formats = vec!["raw".to_owned()];
    let parsed = movie.parsed(tag);
    match parsed {
        Ok(Tag::DefineBits { .. } | Tag::DefineBitsJpeg2 { .. }) => {
            formats.push("png".into());
            if original_jpeg(movie, tag).is_ok() {
                formats.push("jpeg".into());
            }
        }
        Ok(Tag::DefineBitsJpeg3(_) | Tag::DefineBitsLossless(_)) => formats.push("png".into()),
        Ok(Tag::DefineShape(_) | Tag::DefineFont(_) | Tag::DefineFont2(_)) => {
            formats.push("svg".into())
        }
        Ok(Tag::DefineText(_) | Tag::DefineText2(_)) => {
            formats.push("svg".into());
            formats.push("txt".into());
        }
        Ok(Tag::DefineEditText(_)) => formats.push("txt".into()),
        Ok(Tag::DefineSound(sound)) => match sound.format.compression {
            swf::AudioCompression::Mp3 => formats.push("mp3".into()),
            swf::AudioCompression::Uncompressed
            | swf::AudioCompression::UncompressedUnknownEndian
            | swf::AudioCompression::Adpcm => formats.push("wav".into()),
            _ => {}
        },
        Ok(Tag::SoundStreamHead(head) | Tag::SoundStreamHead2(head))
            if head.stream_format.compression == swf::AudioCompression::Mp3 =>
        {
            formats.push("mp3".into())
        }
        Ok(Tag::DefineBinaryData(_)) => formats.push("bin".into()),
        Ok(Tag::DefineFont4(font)) if font.data.is_some() => formats.push("cff".into()),
        _ => {}
    }
    formats
}

pub fn export_resource(movie: &Movie, tag: &TagRecord, format: &str) -> Result<Asset> {
    ensure!(
        movie.tags.iter().any(|t| t.id == tag.id
            && t.start == tag.start
            && t.data_start == tag.data_start
            && t.length == tag.length
            && t.code == tag.code
            && t.path == tag.path),
        "Resource does not belong to this movie"
    );
    if format == "raw" {
        ensure!(
            tag.length <= MAX_ASSET_BYTES,
            "Raw resource exceeds 128 MiB export limit"
        );
        let mut diagnostics = tag.diagnostics.clone();
        if supported_formats(movie, tag).len() == 1 {
            diagnostics.push(Diagnostic::new(
                "raw_only",
                "Resource decoding is unavailable; original tag payload is exported",
                Some(tag.start),
            ));
        }
        return Ok(asset(
            movie.tag_data(tag).to_vec(),
            "application/octet-stream",
            "bin",
            diagnostics,
        ));
    }
    if format == "jpeg" {
        return Ok(asset(
            original_jpeg(movie, tag)?,
            "image/jpeg",
            "jpg",
            vec![],
        ));
    }
    match movie.parsed(tag)? {
        Tag::DefineBits { jpeg_data, .. } if format == "png" => {
            let bytes = combine_jpeg_tables(movie, jpeg_data);
            Ok(asset(
                png(&decode_image(&bytes)?)?,
                "image/png",
                "png",
                vec![],
            ))
        }
        Tag::DefineBitsJpeg2 { jpeg_data, .. } if format == "png" => Ok(asset(
            png(&decode_image(&clean_jpeg(jpeg_data))?)?,
            "image/png",
            "png",
            vec![],
        )),
        Tag::DefineBitsJpeg3(bits) if format == "png" => {
            let mut image = decode_image(&clean_jpeg(bits.data))?;
            if !bits.alpha_data.is_empty() {
                let expected = image.width() as usize * image.height() as usize;
                let alpha = inflate_exact(bits.alpha_data, expected)?;
                for (pixel, alpha) in image.pixels_mut().zip(alpha) {
                    pixel.0[3] = alpha;
                }
            }
            Ok(asset(png(&image)?, "image/png", "png", vec![]))
        }
        Tag::DefineBitsLossless(bits) if format == "png" => {
            Ok(asset(png(&lossless(&bits)?)?, "image/png", "png", vec![]))
        }
        Tag::DefineShape(shape) if format == "svg" => {
            let (svg, diags) = shape_svg(movie, &shape)?;
            Ok(asset(svg.into_bytes(), "image/svg+xml", "svg", diags))
        }
        Tag::DefineFont(font) if format == "svg" => {
            let records = font.glyphs.iter().map(Vec::as_slice).collect::<Vec<_>>();
            Ok(asset(
                font_svg(&records, 1024.0).into_bytes(),
                "image/svg+xml",
                "svg",
                vec![],
            ))
        }
        Tag::DefineFont2(font) if format == "svg" => {
            let records = font
                .glyphs
                .iter()
                .map(|g| g.shape_records.as_slice())
                .collect::<Vec<_>>();
            Ok(asset(
                font_svg(&records, if font.version == 3 { 20480.0 } else { 1024.0 }).into_bytes(),
                "image/svg+xml",
                "svg",
                vec![],
            ))
        }
        Tag::DefineFont4(font) if format == "cff" => Ok(asset(
            font.data
                .context("Font4 has no embedded CFF program")?
                .to_vec(),
            "application/octet-stream",
            "cff",
            vec![],
        )),
        Tag::DefineText(text) | Tag::DefineText2(text) if matches!(format, "svg" | "txt") => {
            let (svg, plain, diags) = text_svg(movie, &text)?;
            if format == "svg" {
                Ok(asset(svg.into_bytes(), "image/svg+xml", "svg", diags))
            } else {
                Ok(asset(
                    plain.into_bytes(),
                    "text/plain; charset=utf-8",
                    "txt",
                    diags,
                ))
            }
        }
        Tag::DefineEditText(text) if format == "txt" => Ok(asset(
            text.initial_text()
                .map(|s| swf_string(movie, s))
                .unwrap_or_default()
                .into_bytes(),
            "text/plain; charset=utf-8",
            "txt",
            vec![],
        )),
        Tag::DefineSound(sound) => export_sound(&sound, format),
        Tag::SoundStreamHead(head) | Tag::SoundStreamHead2(head) if format == "mp3" => {
            ensure!(
                head.stream_format.compression == swf::AudioCompression::Mp3,
                "Stream codec has no MP3 exporter"
            );
            let mut bytes = Vec::new();
            let mut active = false;
            for candidate in &movie.tags {
                if candidate.id == tag.id {
                    active = true;
                    continue;
                }
                if !active
                    || candidate.path.len() != tag.path.len()
                    || candidate.path[..candidate.path.len() - 1] != tag.path[..tag.path.len() - 1]
                {
                    continue;
                }
                if matches!(candidate.code, 18 | 45 | 0) {
                    break;
                }
                if candidate.code == 19 {
                    let block = movie.tag_data(candidate);
                    ensure!(block.len() >= 4, "Truncated MP3 stream block");
                    ensure!(
                        bytes.len() + block.len() - 4 <= MAX_ASSET_BYTES,
                        "MP3 stream exceeds resource limit"
                    );
                    bytes.extend_from_slice(&block[4..]);
                }
            }
            ensure!(!bytes.is_empty(), "MP3 stream contains no blocks");
            Ok(asset(
                bytes,
                "audio/mpeg",
                "mp3",
                vec![Diagnostic::new(
                    "mp3_seek",
                    "Raw MP3 frames exported; Flash seek/sample scheduling is not applied",
                    Some(tag.start),
                )],
            ))
        }
        Tag::DefineBinaryData(data) if format == "bin" => Ok(asset(
            data.data.to_vec(),
            "application/octet-stream",
            "bin",
            vec![],
        )),
        _ => bail!(
            "Format {format} is not supported for {}; export raw bytes instead",
            tag.name
        ),
    }
}

pub fn resource_metadata(movie: &Movie, tag: &TagRecord) -> Value {
    let mut result = json!({"id":tag.id,"name":tag.name,"code":tag.code,"character_id":tag.character_id,"bytes":tag.length,"formats":supported_formats(movie,tag),"diagnostics":tag.diagnostics});
    let specific = match movie.parsed(tag) {
        Ok(Tag::DefineShape(shape)) => {
            json!({"bounds":rect_json(&shape.shape_bounds),"edge_bounds":rect_json(&shape.edge_bounds),"records":shape.shape.len(),"fill_styles":shape.styles.fill_styles.len(),"line_styles":shape.styles.line_styles.len()})
        }
        Ok(Tag::DefineBitsLossless(bits)) => {
            json!({"width":bits.width,"height":bits.height,"format":format!("{:?}",bits.format),"lossless_version":bits.version})
        }
        Ok(Tag::DefineSound(sound)) => {
            json!({"codec":format!("{:?}",sound.format.compression),"sample_rate":sound.format.sample_rate,"channels":if sound.format.is_stereo {2}else{1},"bits":if sound.format.is_16_bit {16}else{8},"sample_count":sound.num_samples})
        }
        Ok(Tag::SoundStreamHead(head) | Tag::SoundStreamHead2(head)) => {
            json!({"codec":format!("{:?}",head.stream_format.compression),"sample_rate":head.stream_format.sample_rate,"channels":if head.stream_format.is_stereo {2}else{1},"samples_per_block":head.num_samples_per_block,"seek":head.latency_seek})
        }
        Ok(Tag::DefineFont(font)) => json!({"glyph_count":font.glyphs.len(),"version":1}),
        Ok(Tag::DefineFont2(font)) => {
            json!({"font_name":swf_string(movie,font.name),"glyph_count":font.glyphs.len(),"version":font.version,"codes":font.glyphs.iter().map(|g|g.code).collect::<Vec<_>>(),"flags":font.flags.bits()})
        }
        Ok(Tag::DefineFont4(font)) => {
            json!({"font_name":swf_string(movie,font.name),"embedded_cff":font.data.is_some(),"bold":font.is_bold,"italic":font.is_italic})
        }
        Ok(Tag::DefineText(text) | Tag::DefineText2(text)) => {
            json!({"bounds":rect_json(&text.bounds),"text":text_svg(movie,&text).map(|t|t.1).unwrap_or_default(),"glyph_count":text.records.iter().map(|r|r.glyphs.len()).sum::<usize>()})
        }
        Ok(Tag::DefineEditText(text)) => {
            json!({"bounds":rect_json(text.bounds()),"text":text.initial_text().map(|s|swf_string(movie,s)),"variable":swf_string(movie,text.variable_name()),"font_id":text.font_id()})
        }
        Ok(Tag::DefineVideoStream(video)) => {
            json!({"width":video.width,"height":video.height,"frame_count":video.num_frames,"codec":format!("{:?}",video.codec),"preview":"not_available"})
        }
        Ok(Tag::DefineBinaryData(data)) => json!({"embedded_bytes":data.data.len()}),
        Ok(Tag::DefineMorphShape(_)) => {
            json!({"preview":"not_available","reason":"Morph interpolation is not implemented in this delivery"})
        }
        Ok(_) => json!({}),
        Err(error) if tag.code == 39 => {
            let data = movie.tag_data(tag);
            json!({"frame_count":data.get(2..4).map(|v|u16::from_le_bytes([v[0],v[1]])),"child_tags":movie.tags.iter().filter(|t|t.path.len()==tag.path.len()+1 && t.path.starts_with(&tag.path)).count(),"preview":"not_available","detail":error.to_string()})
        }
        Err(error) => json!({"parse_error":error.to_string()}),
    };
    if let Some(object) = result.as_object_mut() {
        if object
            .get("formats")
            .and_then(Value::as_array)
            .is_some_and(|formats| formats.len() == 1)
        {
            object.insert("preview".into(), json!("not_available"));
            object.insert("reason".into(),json!("No decoded viewer is available for this tag in this delivery; raw bytes remain available"));
        }
        if let Some(specific) = specific.as_object() {
            object.extend(specific.clone());
        }
    }
    result
}

fn swf_string(movie: &Movie, value: &swf::SwfStr) -> String {
    value
        .to_str_lossy(swf::SwfStr::encoding_for_version(movie.info.version))
        .into_owned()
}
fn rect_json(rect: &swf::Rectangle<swf::Twips>) -> Value {
    json!({"x":rect.x_min.to_pixels(),"y":rect.y_min.to_pixels(),"width":(i64::from(rect.x_max.get())-i64::from(rect.x_min.get())) as f64/20.0,"height":(i64::from(rect.y_max.get())-i64::from(rect.y_min.get())) as f64/20.0})
}
fn clean_jpeg(bytes: &[u8]) -> Vec<u8> {
    let mut out = bytes.to_vec();
    // Early authoring tools accidentally inserted EOI/SOI pairs before JPEG data.
    while out.starts_with(&[0xff, 0xd9, 0xff, 0xd8]) {
        out.drain(..4);
    }
    if out.len() >= 6 && out[2..6] == [0xff, 0xd9, 0xff, 0xd8] {
        out.drain(2..6);
    }
    out
}
fn combine_jpeg_tables(movie: &Movie, jpeg_data: &[u8]) -> Vec<u8> {
    let clean = clean_jpeg(jpeg_data);
    if let Some(tables) = movie.tags.iter().find(|tag| tag.code == 8) {
        let tables = clean_jpeg(movie.tag_data(tables));
        if tables.len() >= 4
            && tables.starts_with(&[0xff, 0xd8])
            && tables.ends_with(&[0xff, 0xd9])
            && clean.starts_with(&[0xff, 0xd8])
        {
            let mut combined = tables[..tables.len() - 2].to_vec();
            combined.extend_from_slice(&clean[2..]);
            return combined;
        }
    }
    clean
}
fn original_jpeg(movie: &Movie, tag: &TagRecord) -> Result<Vec<u8>> {
    let bytes = match movie.parsed(tag)? {
        Tag::DefineBits { jpeg_data, .. } => combine_jpeg_tables(movie, jpeg_data),
        Tag::DefineBitsJpeg2 { jpeg_data, .. } => clean_jpeg(jpeg_data),
        _ => bail!(
            "Original JPEG export is only available for DefineBits and JPEG2 without an alpha plane"
        ),
    };
    ensure!(
        bytes.starts_with(&[0xff, 0xd8]),
        "Embedded stream is not JPEG; use PNG or raw export"
    );
    // Decode only to verify format and dimensions. Return the stored compressed
    // stream, including shared tables when required, without re-encoding it.
    decode_image(&bytes).context("Embedded JPEG is invalid or exceeds image limits")?;
    Ok(bytes)
}
fn decode_image(bytes: &[u8]) -> Result<RgbaImage> {
    let reader = ImageReader::new(Cursor::new(bytes)).with_guessed_format()?;
    let format = reader.format().context("Unrecognized image format")?;
    let (width, height) = ImageReader::with_format(Cursor::new(bytes), format).into_dimensions()?;
    check_pixels(width as usize, height as usize)?;
    let mut reader = ImageReader::with_format(Cursor::new(bytes), format);
    let mut limits = image::Limits::default();
    limits.max_alloc = Some(MAX_ASSET_BYTES as u64);
    limits.max_image_width = Some(16384);
    limits.max_image_height = Some(16384);
    reader.limits(limits);
    Ok(reader.decode()?.into_rgba8())
}
fn png(image: &RgbaImage) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    image::codecs::png::PngEncoder::new(&mut bytes).write_image(
        image.as_raw(),
        image.width(),
        image.height(),
        image::ExtendedColorType::Rgba8,
    )?;
    Ok(bytes)
}
fn check_pixels(width: usize, height: usize) -> Result<()> {
    ensure!(
        width > 0
            && height > 0
            && width <= 16384
            && height <= 16384
            && width.checked_mul(height).is_some_and(|n| n <= MAX_PIXELS),
        "Image exceeds 16 megapixel/16384 dimension limit"
    );
    Ok(())
}
fn inflate_exact(data: &[u8], expected: usize) -> Result<Vec<u8>> {
    ensure!(
        expected <= MAX_ASSET_BYTES,
        "Resource exceeds 128 MiB decoded limit"
    );
    let mut decoder = flate2::read::ZlibDecoder::new(data);
    let mut bytes = Vec::new();
    decoder
        .by_ref()
        .take(expected as u64 + 1)
        .read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() == expected,
        "Decoded resource length differs from expected length"
    );
    ensure!(
        decoder.total_in() as usize == data.len(),
        "Trailing bytes in resource zlib stream"
    );
    Ok(bytes)
}
fn unpremultiply(c: u8, a: u8) -> u8 {
    if a == 0 {
        0
    } else {
        ((u32::from(c) * 255 + u32::from(a) / 2) / u32::from(a)).min(255) as u8
    }
}
fn lossless(bits: &swf::DefineBitsLossless<'_>) -> Result<RgbaImage> {
    let w = bits.width as usize;
    let h = bits.height as usize;
    check_pixels(w, h)?;
    let mut rgba = vec![0u8; w * h * 4];
    match bits.format {
        BitmapFormat::Rgb32 => {
            let raw = inflate_exact(&bits.data, w * h * 4)?;
            for (target, source) in rgba
                .as_chunks_mut::<4>()
                .0
                .iter_mut()
                .zip(raw.as_chunks::<4>().0.iter())
            {
                let a = if bits.version == 2 { source[0] } else { 255 };
                target.copy_from_slice(&[
                    unpremultiply(source[1], a),
                    unpremultiply(source[2], a),
                    unpremultiply(source[3], a),
                    a,
                ]);
            }
        }
        BitmapFormat::ColorMap8 { num_colors } => {
            let count = usize::from(num_colors) + 1;
            let size = if bits.version == 2 { 4 } else { 3 };
            let stride = w.div_ceil(4) * 4;
            let raw = inflate_exact(&bits.data, count * size + stride * h)?;
            for y in 0..h {
                for x in 0..w {
                    let index = raw[count * size + y * stride + x] as usize;
                    ensure!(index < count, "Invalid lossless palette index");
                    let color = &raw[index * size..index * size + size];
                    let a = if size == 4 { color[3] } else { 255 };
                    rgba[(y * w + x) * 4..(y * w + x + 1) * 4].copy_from_slice(&[
                        unpremultiply(color[0], a),
                        unpremultiply(color[1], a),
                        unpremultiply(color[2], a),
                        a,
                    ]);
                }
            }
        }
        BitmapFormat::Rgb15 => {
            let stride = (w * 2).div_ceil(4) * 4;
            let raw = inflate_exact(&bits.data, stride * h)?;
            for y in 0..h {
                for x in 0..w {
                    let i = y * stride + x * 2;
                    let c = u16::from_le_bytes([raw[i], raw[i + 1]]);
                    let expand = |v: u16| ((v << 3) | (v >> 2)) as u8;
                    rgba[(y * w + x) * 4..(y * w + x + 1) * 4].copy_from_slice(&[
                        expand((c >> 10) & 31),
                        expand((c >> 5) & 31),
                        expand(c & 31),
                        255,
                    ]);
                }
            }
        }
    }
    RgbaImage::from_raw(bits.width as u32, bits.height as u32, rgba)
        .context("Invalid decoded bitmap")
}

type Point = (i64, i64);
#[derive(Clone)]
struct Edge {
    start: Point,
    end: Point,
    control: Option<Point>,
}
impl Edge {
    fn reversed(&self) -> Self {
        Self {
            start: self.end,
            end: self.start,
            control: self.control,
        }
    }
}
fn point(value: swf::Point<swf::Twips>) -> Point {
    (i64::from(value.x.get()), i64::from(value.y.get()))
}
fn edge_command(out: &mut String, edge: &Edge, scale: f64) {
    if let Some((x, y)) = edge.control {
        let _ = write!(
            out,
            "Q{} {} {} {} ",
            x as f64 * scale,
            y as f64 * scale,
            edge.end.0 as f64 * scale,
            edge.end.1 as f64 * scale
        );
    } else {
        let _ = write!(
            out,
            "L{} {} ",
            edge.end.0 as f64 * scale,
            edge.end.1 as f64 * scale
        );
    }
}
fn connected_path(edges: &[Edge], scale: f64, close: bool) -> String {
    let mut starts: HashMap<Point, Vec<usize>> = HashMap::new();
    for (i, edge) in edges.iter().enumerate() {
        starts.entry(edge.start).or_default().push(i);
    }
    let mut used = vec![false; edges.len()];
    let mut path = String::new();
    for first in 0..edges.len() {
        if used[first] {
            continue;
        }
        let origin = edges[first].start;
        let mut index = first;
        let _ = write!(
            path,
            "M{} {} ",
            origin.0 as f64 * scale,
            origin.1 as f64 * scale
        );
        loop {
            used[index] = true;
            let edge = &edges[index];
            edge_command(&mut path, edge, scale);
            if edge.end == origin {
                if close {
                    path.push_str("Z ");
                }
                break;
            }
            let next = starts.get_mut(&edge.end).and_then(|indices| {
                while indices.last().is_some_and(|i| used[*i]) {
                    indices.pop();
                }
                indices.pop()
            });
            if let Some(next) = next {
                index = next;
            } else {
                break;
            }
        }
    }
    path
}
fn svg_header(rect: &swf::Rectangle<swf::Twips>) -> String {
    let x = rect.x_min.to_pixels();
    let y = rect.y_min.to_pixels();
    let w = ((i64::from(rect.x_max.get()) - i64::from(rect.x_min.get())) as f64 / 20.0).max(1.0);
    let h = ((i64::from(rect.y_max.get()) - i64::from(rect.y_min.get())) as f64 / 20.0).max(1.0);
    format!(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" xmlns:xlink=\"http://www.w3.org/1999/xlink\" viewBox=\"{x} {y} {w} {h}\" width=\"{w}\" height=\"{h}\">"
    )
}
fn rgba(color: Color) -> String {
    format!(
        "rgba({},{},{},{})",
        color.r,
        color.g,
        color.b,
        f64::from(color.a) / 255.0
    )
}
fn matrix(matrix: &swf::Matrix, translation_scale: f64) -> String {
    format!(
        "matrix({} {} {} {} {} {})",
        matrix.a.to_f32(),
        matrix.b.to_f32(),
        matrix.c.to_f32(),
        matrix.d.to_f32(),
        f64::from(matrix.tx.get()) * translation_scale,
        f64::from(matrix.ty.get()) * translation_scale
    )
}
fn fill_paint(
    movie: &Movie,
    style: &FillStyle,
    id: &str,
    defs: &mut String,
    diags: &mut Vec<Diagnostic>,
) -> Result<String> {
    match style {
        FillStyle::Color(color) => Ok(rgba(*color)),
        FillStyle::LinearGradient(gradient)
        | FillStyle::RadialGradient(gradient)
        | FillStyle::FocalGradient { gradient, .. } => {
            let transform = format!(
                "matrix({} {} {} {} {} {})",
                gradient.matrix.a.to_f32() / 20.0,
                gradient.matrix.b.to_f32() / 20.0,
                gradient.matrix.c.to_f32() / 20.0,
                gradient.matrix.d.to_f32() / 20.0,
                gradient.matrix.tx.to_pixels(),
                gradient.matrix.ty.to_pixels()
            );
            let spread = match gradient.spread {
                swf::GradientSpread::Pad => "pad",
                swf::GradientSpread::Reflect => "reflect",
                swf::GradientSpread::Repeat => "repeat",
            };
            let interpolation = if gradient.interpolation == swf::GradientInterpolation::LinearRgb {
                "linearRGB"
            } else {
                "sRGB"
            };
            let linear = matches!(style, FillStyle::LinearGradient(_));
            let element = if linear {
                "linearGradient"
            } else {
                "radialGradient"
            };
            let geometry = if linear {
                "x1=\"-16384\" y1=\"0\" x2=\"16384\" y2=\"0\"".into()
            } else {
                let focal = if let FillStyle::FocalGradient { focal_point, .. } = style {
                    focal_point.to_f32() as f64 * 16384.0
                } else {
                    0.0
                };
                format!("cx=\"0\" cy=\"0\" r=\"16384\" fx=\"{focal}\" fy=\"0\"")
            };
            let _ = write!(
                defs,
                "<{element} id=\"{id}\" gradientUnits=\"userSpaceOnUse\" {geometry} gradientTransform=\"{transform}\" spreadMethod=\"{spread}\" color-interpolation=\"{interpolation}\">"
            );
            for record in &gradient.records {
                let _ = write!(
                    defs,
                    "<stop offset=\"{}\" stop-color=\"{}\"/>",
                    f64::from(record.ratio) / 255.0,
                    rgba(record.color)
                );
            }
            let _ = write!(defs, "</{element}>");
            Ok(format!("url(#{id})"))
        }
        FillStyle::Bitmap {
            id: bitmap_id,
            matrix: transform,
            is_smoothed,
            is_repeating,
        } => {
            let bitmap = movie
                .tags
                .iter()
                .find(|t| t.character_id == Some(*bitmap_id) && t.category == "images");
            if let Some(bitmap) = bitmap {
                match export_resource(movie, bitmap, "png") {
                    Ok(png) => {
                        let decoded = decode_image(&png.bytes)?;
                        let w = decoded.width();
                        let h = decoded.height();
                        let encoded = STANDARD.encode(&png.bytes);
                        let transform = format!(
                            "matrix({} {} {} {} {} {})",
                            transform.a.to_f32() / 20.0,
                            transform.b.to_f32() / 20.0,
                            transform.c.to_f32() / 20.0,
                            transform.d.to_f32() / 20.0,
                            transform.tx.to_pixels(),
                            transform.ty.to_pixels()
                        );
                        let rendering = if *is_smoothed { "auto" } else { "pixelated" };
                        let _ = write!(
                            defs,
                            "<pattern id=\"{id}\" patternUnits=\"userSpaceOnUse\" width=\"{w}\" height=\"{h}\" patternTransform=\"{transform}\"><image width=\"{w}\" height=\"{h}\" image-rendering=\"{rendering}\" xlink:href=\"data:image/png;base64,{encoded}\"/></pattern>"
                        );
                        if !*is_repeating {
                            diags.push(Diagnostic::new(
                                "bitmap_clipping",
                                "Clipped bitmap fills currently repeat in the SVG preview",
                                None,
                            ));
                        }
                        Ok(format!("url(#{id})"))
                    }
                    Err(error) => {
                        diags.push(Diagnostic::new("bitmap_fill", error.to_string(), None));
                        Ok("none".into())
                    }
                }
            } else {
                diags.push(Diagnostic::new(
                    "bitmap_fill",
                    format!("Bitmap {bitmap_id} is missing"),
                    None,
                ));
                Ok("none".into())
            }
        }
    }
}

fn shape_svg(movie: &Movie, shape: &swf::Shape) -> Result<(String, Vec<Diagnostic>)> {
    let mut fills = shape.styles.fill_styles.clone();
    let mut lines = shape.styles.line_styles.clone();
    let mut fill_base = 0;
    let mut line_base = 0;
    let mut f0 = 0usize;
    let mut f1 = 0usize;
    let mut line = 0usize;
    let mut pos = (0i64, 0i64);
    let mut fill_edges: BTreeMap<usize, Vec<Edge>> = BTreeMap::new();
    let mut line_edges: BTreeMap<usize, Vec<Edge>> = BTreeMap::new();
    for record in &shape.shape {
        let edge = match record {
            ShapeRecord::StyleChange(style) => {
                if let Some(p) = style.move_to {
                    pos = point(p);
                }
                if let Some(index) = style.fill_style_0 {
                    f0 = if index == 0 {
                        0
                    } else {
                        index as usize + fill_base
                    };
                }
                if let Some(index) = style.fill_style_1 {
                    f1 = if index == 0 {
                        0
                    } else {
                        index as usize + fill_base
                    };
                }
                if let Some(index) = style.line_style {
                    line = if index == 0 {
                        0
                    } else {
                        index as usize + line_base
                    };
                }
                if let Some(new) = &style.new_styles {
                    fill_base = fills.len();
                    line_base = lines.len();
                    fills.extend(new.fill_styles.clone());
                    lines.extend(new.line_styles.clone());
                }
                continue;
            }
            ShapeRecord::StraightEdge { delta } => Edge {
                start: pos,
                end: (
                    pos.0 + i64::from(delta.dx.get()),
                    pos.1 + i64::from(delta.dy.get()),
                ),
                control: None,
            },
            ShapeRecord::CurvedEdge {
                control_delta,
                anchor_delta,
            } => {
                let control = (
                    pos.0 + i64::from(control_delta.dx.get()),
                    pos.1 + i64::from(control_delta.dy.get()),
                );
                Edge {
                    start: pos,
                    end: (
                        control.0 + i64::from(anchor_delta.dx.get()),
                        control.1 + i64::from(anchor_delta.dy.get()),
                    ),
                    control: Some(control),
                }
            }
        };
        pos = edge.end;
        if f0 != 0 {
            fill_edges.entry(f0).or_default().push(edge.reversed());
        }
        if f1 != 0 {
            fill_edges.entry(f1).or_default().push(edge.clone());
        }
        if line != 0 {
            line_edges.entry(line).or_default().push(edge);
        }
    }
    let mut defs = String::new();
    let mut body = String::new();
    let mut diags = Vec::new();
    let rule = if shape.flags.contains(swf::ShapeFlag::NON_ZERO_WINDING_RULE) {
        "nonzero"
    } else {
        "evenodd"
    };
    for (index, edges) in fill_edges {
        let style = fills
            .get(index - 1)
            .context("Shape refers to missing fill style")?;
        let paint = fill_paint(movie, style, &format!("fill{index}"), &mut defs, &mut diags)?;
        let path = connected_path(&edges, 0.05, true);
        let _ = write!(
            body,
            "<path d=\"{path}\" fill=\"{paint}\" fill-rule=\"{rule}\"/>"
        );
    }
    for (index, edges) in line_edges {
        let style = lines
            .get(index - 1)
            .context("Shape refers to missing line style")?;
        let paint = fill_paint(
            movie,
            style.fill_style(),
            &format!("stroke{index}"),
            &mut defs,
            &mut diags,
        )?;
        let cap = match style.start_cap() {
            swf::LineCapStyle::Round => "round",
            swf::LineCapStyle::None => "butt",
            swf::LineCapStyle::Square => "square",
        };
        let (join, miter) = match style.join_style() {
            swf::LineJoinStyle::Round => ("round", 4.0),
            swf::LineJoinStyle::Bevel => ("bevel", 4.0),
            swf::LineJoinStyle::Miter(v) => ("miter", v.to_f32() as f64),
        };
        if style.start_cap() != style.end_cap() {
            diags.push(Diagnostic::new(
                "stroke_caps",
                "Different start/end caps are approximated by SVG's shared linecap",
                None,
            ));
        }
        if !style.allow_scale_x() || !style.allow_scale_y() {
            diags.push(Diagnostic::new(
                "stroke_scaling",
                "SWF directional stroke scaling is approximated in static SVG",
                None,
            ));
        }
        let path = connected_path(&edges, 0.05, style.allow_close());
        let _ = write!(
            body,
            "<path d=\"{path}\" fill=\"none\" stroke=\"{paint}\" stroke-width=\"{}\" stroke-linecap=\"{cap}\" stroke-linejoin=\"{join}\" stroke-miterlimit=\"{miter}\"/>",
            style.width().to_pixels().max(0.05)
        );
    }
    Ok((
        format!(
            "{}<defs>{defs}</defs>{body}</svg>",
            svg_header(&shape.shape_bounds)
        ),
        diags,
    ))
}

fn glyph_path(records: &[ShapeRecord], scale: f64) -> String {
    let mut out = String::new();
    let mut pos = (0i64, 0i64);
    let mut opened = false;
    for record in records {
        match record {
            ShapeRecord::StyleChange(style) => {
                if let Some(p) = style.move_to {
                    if opened {
                        out.push_str("Z ");
                    }
                    pos = point(p);
                    let _ = write!(out, "M{} {} ", pos.0 as f64 * scale, pos.1 as f64 * scale);
                    opened = true;
                }
            }
            ShapeRecord::StraightEdge { delta } => {
                if !opened {
                    out.push_str("M0 0 ");
                    opened = true;
                }
                pos = (
                    pos.0 + i64::from(delta.dx.get()),
                    pos.1 + i64::from(delta.dy.get()),
                );
                let _ = write!(out, "L{} {} ", pos.0 as f64 * scale, pos.1 as f64 * scale);
            }
            ShapeRecord::CurvedEdge {
                control_delta,
                anchor_delta,
            } => {
                if !opened {
                    out.push_str("M0 0 ");
                    opened = true;
                }
                let c = (
                    pos.0 + i64::from(control_delta.dx.get()),
                    pos.1 + i64::from(control_delta.dy.get()),
                );
                pos = (
                    c.0 + i64::from(anchor_delta.dx.get()),
                    c.1 + i64::from(anchor_delta.dy.get()),
                );
                let _ = write!(
                    out,
                    "Q{} {} {} {} ",
                    c.0 as f64 * scale,
                    c.1 as f64 * scale,
                    pos.0 as f64 * scale,
                    pos.1 as f64 * scale
                );
            }
        }
    }
    if opened {
        out.push('Z');
    }
    out
}
fn font_svg(glyphs: &[&[ShapeRecord]], em: f64) -> String {
    let columns = 16usize;
    let rows = glyphs.len().div_ceil(columns).max(1);
    let mut svg = format!(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 1280 {}\"><rect width=\"100%\" height=\"100%\" fill=\"#fff\"/>",
        rows * 100
    );
    for (i, records) in glyphs.iter().enumerate() {
        let x = i % columns * 80;
        let y = i / columns * 100;
        let path = glyph_path(records, 64.0 / em);
        let _ = write!(
            svg,
            "<g transform=\"translate({} {})\"><text y=\"12\" font-size=\"10\">{i}</text><path transform=\"translate(4 72)\" d=\"{path}\" fill=\"#111\" fill-rule=\"evenodd\"/></g>",
            x, y
        );
    }
    svg.push_str("</svg>");
    svg
}
fn text_svg(movie: &Movie, text: &swf::Text) -> Result<(String, String, Vec<Diagnostic>)> {
    let mut fonts = HashMap::new();
    let mut legacy = HashMap::new();
    for tag in &movie.tags {
        if matches!(tag.code, 10 | 48 | 75) {
            match movie.parsed(tag) {
                Ok(Tag::DefineFont2(font)) => {
                    fonts.insert(font.id, font);
                }
                Ok(Tag::DefineFont(font)) => {
                    legacy.insert(font.id, font);
                }
                _ => {}
            }
        }
    }
    let mut body = String::new();
    let mut plain = String::new();
    let mut notes = Vec::new();
    let mut font_id = 0;
    let mut x = 0i64;
    let mut y = 0i64;
    let mut height = 240i32;
    let mut color = swf::Color {
        r: 0,
        g: 0,
        b: 0,
        a: 255,
    };
    for record in &text.records {
        if let Some(id) = record.font_id {
            font_id = id;
        }
        if let Some(v) = record.x_offset {
            x = i64::from(v.get());
        }
        if let Some(v) = record.y_offset {
            y = i64::from(v.get());
        }
        if let Some(v) = record.height {
            height = v.get();
        }
        if let Some(v) = record.color {
            color = v;
        }
        for entry in &record.glyphs {
            let data = fonts
                .get(&font_id)
                .and_then(|font| {
                    font.glyphs.get(entry.index as usize).map(|glyph| {
                        (
                            glyph.shape_records.as_slice(),
                            if font.version == 3 { 20480.0 } else { 1024.0 },
                            Some(glyph.code),
                        )
                    })
                })
                .or_else(|| {
                    legacy.get(&font_id).and_then(|font| {
                        font.glyphs
                            .get(entry.index as usize)
                            .map(|records| (records.as_slice(), 1024.0, None))
                    })
                });
            if let Some((records, em, code)) = data {
                let path = glyph_path(records, f64::from(height) / 20.0 / em);
                let _ = write!(
                    body,
                    "<path transform=\"translate({} {})\" d=\"{path}\" fill=\"{}\" fill-rule=\"evenodd\"/>",
                    x as f64 / 20.0,
                    y as f64 / 20.0,
                    rgba(color)
                );
                plain.push(
                    code.and_then(|c| char::from_u32(c as u32))
                        .unwrap_or('\u{fffd}'),
                );
            } else {
                plain.push('\u{fffd}');
                notes.push(Diagnostic::new(
                    "missing_glyph",
                    format!("Font {font_id} glyph {} unavailable", entry.index),
                    None,
                ));
            }
            x += i64::from(entry.advance);
        }
    }
    Ok((
        format!(
            "{}<g transform=\"{}\">{body}</g></svg>",
            svg_header(&text.bounds),
            matrix(&text.matrix, 0.05)
        ),
        plain,
        notes,
    ))
}

fn export_sound(sound: &swf::Sound<'_>, format: &str) -> Result<Asset> {
    let channels = if sound.format.is_stereo { 2usize } else { 1 };
    let rate = sound.format.sample_rate as u32;
    let mut notes = Vec::new();
    if sound.format.compression == swf::AudioCompression::Mp3 && format == "mp3" {
        ensure!(sound.data.len() >= 2, "Truncated MP3 seek header");
        return Ok(asset(
            sound.data[2..].to_vec(),
            "audio/mpeg",
            "mp3",
            vec![Diagnostic::new(
                "mp3_seek",
                "Raw MP3 frames exported; Flash seek scheduling is not applied",
                None,
            )],
        ));
    }
    ensure!(
        format == "wav",
        "Sound codec does not support format {format}"
    );
    let (bytes, bits) = match sound.format.compression {
        swf::AudioCompression::Adpcm => (
            adpcm(sound.data, channels, sound.num_samples as usize)?,
            16u16,
        ),
        swf::AudioCompression::Uncompressed | swf::AudioCompression::UncompressedUnknownEndian => {
            let bits = if sound.format.is_16_bit { 16u16 } else { 8u16 };
            let expected = (sound.num_samples as usize)
                .checked_mul(channels)
                .and_then(|n| n.checked_mul(bits as usize / 8))
                .context("PCM length overflow")?;
            ensure!(
                expected <= MAX_ASSET_BYTES && sound.data.len() == expected,
                "PCM sample count differs from payload length or exceeds limit"
            );
            if sound.format.compression == swf::AudioCompression::UncompressedUnknownEndian
                && bits == 16
            {
                notes.push(Diagnostic::new("pcm_endian","Native-endian PCM is interpreted as little endian; authoring platform is not encoded",None));
            }
            (sound.data.to_vec(), bits)
        }
        _ => bail!("Sound codec is not decoded; export raw bytes"),
    };
    let mut wav = Vec::with_capacity(44 + bytes.len());
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36u32 + bytes.len() as u32).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16u32.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes());
    wav.extend_from_slice(&(channels as u16).to_le_bytes());
    wav.extend_from_slice(&rate.to_le_bytes());
    let align = channels as u16 * bits / 8;
    wav.extend_from_slice(&(rate * u32::from(align)).to_le_bytes());
    wav.extend_from_slice(&align.to_le_bytes());
    wav.extend_from_slice(&bits.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&(bytes.len() as u32).to_le_bytes());
    wav.extend_from_slice(&bytes);
    if bytes.len() % 2 != 0 {
        wav.push(0);
        let length = wav.len() as u32 - 8;
        wav[4..8].copy_from_slice(&length.to_le_bytes());
    }
    Ok(asset(wav, "audio/wav", "wav", notes))
}
fn adpcm(data: &[u8], channels: usize, samples: usize) -> Result<Vec<u8>> {
    let expected = samples
        .checked_mul(channels)
        .and_then(|n| n.checked_mul(2))
        .context("ADPCM sample count overflow")?;
    ensure!(
        expected <= MAX_ASSET_BYTES,
        "ADPCM decoded samples exceed limit"
    );
    const STEPS: [i32; 89] = [
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60,
        66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371,
        408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878,
        2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845,
        8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086,
        29794, 32767,
    ];
    const INDEX2: [i32; 2] = [-1, 2];
    const INDEX3: [i32; 4] = [-1, -1, 2, 4];
    const INDEX4: [i32; 8] = [-1, -1, -1, -1, 2, 4, 6, 8];
    const INDEX5: [i32; 16] = [-1, -1, -1, -1, -1, -1, -1, -1, 1, 2, 4, 6, 8, 10, 13, 16];
    let mut bits = Bits::new(data);
    let width = bits.unsigned(2)? as usize + 2;
    let table: &[i32] = match width {
        2 => &INDEX2,
        3 => &INDEX3,
        4 => &INDEX4,
        _ => &INDEX5,
    };
    let mut out = Vec::with_capacity(expected);
    let mut remaining = samples;
    while remaining > 0 {
        let packet = remaining.min(4096);
        let mut current = vec![0i32; channels];
        let mut indices = vec![0usize; channels];
        for ch in 0..channels {
            current[ch] = bits.signed(16)?;
            indices[ch] = bits.unsigned(6)? as usize;
            ensure!(indices[ch] < 89, "Invalid ADPCM step index");
            out.extend_from_slice(&(current[ch] as i16).to_le_bytes());
        }
        for _ in 1..packet {
            for ch in 0..channels {
                let code = bits.unsigned(width)? as usize;
                let magnitude = code & ((1 << (width - 1)) - 1);
                let step = STEPS[indices[ch]];
                let mut difference = step >> (width - 1);
                for bit in 0..width - 1 {
                    if magnitude & (1 << (width - 2 - bit)) != 0 {
                        difference += step >> bit;
                    }
                }
                if code & (1 << (width - 1)) != 0 {
                    current[ch] -= difference;
                } else {
                    current[ch] += difference;
                }
                current[ch] = current[ch].clamp(-32768, 32767);
                indices[ch] = (indices[ch] as i32 + table[magnitude]).clamp(0, 88) as usize;
                out.extend_from_slice(&(current[ch] as i16).to_le_bytes());
            }
        }
        remaining -= packet;
    }
    Ok(out)
}
