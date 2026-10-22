use std::{
    io::{Cursor, Write},
    path::Path,
};
use swf_core::{Movie, export_resource, resource_metadata, supported_formats};

fn bits(fields: &[(u32, usize)]) -> Vec<u8> {
    let mut result = Vec::new();
    let mut position = 0;
    for &(value, width) in fields {
        for bit in (0..width).rev() {
            if position % 8 == 0 {
                result.push(0);
            }
            let last = result.len() - 1;
            result[last] |= (((value >> bit) & 1) as u8) << (7 - position % 8);
            position += 1;
        }
    }
    result
}
fn tag(code: u16, payload: &[u8]) -> Vec<u8> {
    let mut bytes = Vec::new();
    if payload.len() < 63 {
        bytes.extend_from_slice(&((code << 6) | payload.len() as u16).to_le_bytes());
    } else {
        bytes.extend_from_slice(&((code << 6) | 63).to_le_bytes());
        bytes.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    }
    bytes.extend_from_slice(payload);
    bytes
}
fn movie(tags: &[Vec<u8>]) -> Vec<u8> {
    let mut bytes = b"FWS\x0d\0\0\0\0".to_vec();
    bytes.extend(bits(&[(12, 5), (0, 12), (2000, 12), (0, 12), (1000, 12)]));
    bytes.extend_from_slice(&(24u16 * 256).to_le_bytes());
    bytes.extend_from_slice(&1u16.to_le_bytes());
    for t in tags {
        bytes.extend_from_slice(t);
    }
    bytes.extend(tag(0, &[]));
    let len = bytes.len() as u32;
    bytes[4..8].copy_from_slice(&len.to_le_bytes());
    bytes
}
fn zlib(data: &[u8]) -> Vec<u8> {
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
    encoder.write_all(data).unwrap();
    encoder.finish().unwrap()
}
fn cws(data: &[u8]) -> Vec<u8> {
    let mut bytes = data[..8].to_vec();
    bytes[..3].copy_from_slice(b"CWS");
    bytes.extend(zlib(&data[8..]));
    bytes
}
fn zws(data: &[u8]) -> Vec<u8> {
    let mut compressed = Vec::new();
    lzma_rs::lzma_compress(&mut Cursor::new(&data[8..]), &mut compressed).unwrap();
    let mut bytes = data[..8].to_vec();
    bytes[..3].copy_from_slice(b"ZWS");
    bytes.extend_from_slice(&((compressed.len() - 13) as u32).to_le_bytes());
    bytes.extend_from_slice(&compressed[..5]);
    bytes.extend_from_slice(&compressed[13..]);
    bytes
}

#[test]
fn compression_and_original_are_exact() {
    let fws = movie(&[tag(12, &[0])]);
    let compressed = cws(&fws);
    let lzma = zws(&fws);
    for input in [fws.as_slice(), compressed.as_slice(), lzma.as_slice()] {
        let parsed = Movie::from_bytes(input, "Español.swf").unwrap();
        assert_eq!(parsed.original, input);
        assert_eq!(parsed.normalized, fws);
        assert_eq!(parsed.info.width, 100.0);
        assert_eq!(parsed.info.height, 50.0);
        assert_eq!(parsed.info.frame_rate, 24.0);
        assert_eq!(parsed.info.tag_count, 2);
    }
}

#[test]
fn container_writer_preserves_unknown_nested_tags_metadata_and_original() {
    use swf_core::ContainerCompression::*;
    let mut sprite = vec![7, 0, 1, 0];
    sprite.extend(tag(511, &[0, 255, 13, 37]));
    sprite.extend(tag(0, &[]));
    let mut input = movie(&[
        tag(39, &sprite),
        tag(77, b"<metadata vendor='unknown'/>\0"),
        tag(1, &[]),
    ]);
    input.extend([0, 37, 255]); // Untouched bytes after End are retained too.
    let length = input.len() as u32;
    input[4..8].copy_from_slice(&length.to_le_bytes());
    for original in [input.clone(), cws(&input), zws(&input)] {
        let parsed = Movie::from_bytes(&original, "roundtrip.swf").unwrap();
        assert_eq!(parsed.encoded(Original).unwrap(), original);
        for compression in [Fws, Cws, Zws] {
            let written = parsed.encoded(compression).unwrap();
            let reparsed = Movie::from_bytes(&written, "roundtrip.swf").unwrap();
            assert_eq!(reparsed.normalized, input);
            assert_eq!(reparsed.tags.len(), parsed.tags.len());
            assert_eq!(reparsed.info.version, parsed.info.version);
            assert_eq!(
                parsed.original, original,
                "writing must not mutate its original"
            );
        }
    }
    input[3] = 5;
    let old = Movie::from_bytes(&input, "flash5.swf").unwrap();
    assert!(old.encoded(Cws).is_err());
    assert!(old.encoded(Zws).is_err());
    assert_eq!(old.encoded(Fws).unwrap()[3], 5);
}

#[test]
fn preserves_nested_offsets_unknown_tags_and_symbols() {
    let action = [0x06, 0];
    let mut sprite = vec![7, 0, 1, 0];
    sprite.extend(tag(12, &action));
    sprite.extend(tag(511, &[8, 9, 10]));
    sprite.extend(tag(0, &[]));
    let mut symbols = vec![1, 0, 7, 0];
    symbols.extend_from_slice(b"Player\0");
    let parsed =
        Movie::from_bytes(&movie(&[tag(39, &sprite), tag(76, &symbols)]), "nested.swf").unwrap();
    assert_eq!(parsed.tags[0].symbol.as_deref(), Some("Player"));
    let nested = &parsed.tags[1];
    assert_eq!(nested.path, vec![0, 0]);
    assert_eq!(nested.id, "tag:0/0");
    assert_eq!(parsed.tag_data(nested), action);
    assert_eq!(parsed.tags[2].code, 511);
    assert_eq!(parsed.tags[2].diagnostics[0].code, "unknown_tag");
    assert_eq!(parsed.tag_data(&parsed.tags[2]), [8, 9, 10]);
}

#[test]
fn rejects_truncated_ranges_and_decompression_bombs() {
    let mut data = movie(&[tag(12, &[0])]);
    let len = data.len();
    data[len - 5] = (12u16 << 6 | 62) as u8;
    assert!(Movie::from_bytes(&data, "bad.swf").is_err());
    let mut bomb = cws(&movie(&[tag(12, &vec![0; 20000])]));
    bomb[4..8].copy_from_slice(&20u32.to_le_bytes());
    assert!(Movie::from_bytes(&bomb, "bomb.swf").is_err());
    let mut wrong = zws(&movie(&[]));
    wrong[8..12].copy_from_slice(&99u32.to_le_bytes());
    assert!(Movie::from_bytes(&wrong, "length.swf").is_err());
    let mut dictionary = zws(&movie(&[]));
    dictionary[13..17].copy_from_slice(&(128u32 * 1024 * 1024).to_le_bytes());
    assert!(Movie::from_bytes(&dictionary, "dict.swf").is_err());
}

#[test]
fn depth_is_bounded_and_missing_end_is_diagnostic() {
    let mut nested = tag(12, &[0]);
    for _ in 0..65 {
        let mut payload = vec![1, 0, 1, 0];
        payload.extend(nested);
        payload.extend(tag(0, &[]));
        nested = tag(39, &payload);
    }
    assert!(Movie::from_bytes(&movie(&[nested]), "deep.swf").is_err());
    let mut missing = movie(&[tag(12, &[0])]);
    missing.truncate(missing.len() - 2);
    let len = missing.len() as u32;
    missing[4..8].copy_from_slice(&len.to_le_bytes());
    let parsed = Movie::from_bytes(&missing, "missing.swf").unwrap();
    assert!(parsed.diagnostics.iter().any(|d| d.code == "missing_end"));
}

#[test]
fn lossless_palette_uses_uncompressed_size_and_row_padding() {
    let mut payload = vec![1, 0, 3, 3, 0, 1, 0, 1];
    let raw = [64, 0, 0, 128, 0, 255, 0, 255, 0, 1, 0, 0];
    payload.extend(zlib(&raw));
    let parsed = Movie::from_bytes(&movie(&[tag(36, &payload)]), "palette.swf").unwrap();
    let asset = export_resource(&parsed, &parsed.tags[0], "png").unwrap();
    let img = image::load_from_memory(&asset.bytes).unwrap().into_rgba8();
    assert_eq!(img.dimensions(), (3, 1));
    assert_eq!(img.get_pixel(0, 0).0, [128, 0, 0, 128]);
    assert_eq!(img.get_pixel(1, 0).0, [0, 255, 0, 255]);
    assert_eq!(img.get_pixel(2, 0).0, [128, 0, 0, 128]);
}

#[test]
fn lossless_argb_and_15bit_decode_without_losing_alpha() {
    let mut payload = vec![1, 0, 5, 1, 0, 1, 0];
    payload.extend(zlib(&[128, 32, 64, 0]));
    let parsed = Movie::from_bytes(&movie(&[tag(36, &payload)]), "alpha.swf").unwrap();
    let png = export_resource(&parsed, &parsed.tags[0], "png").unwrap();
    assert_eq!(
        image::load_from_memory(&png.bytes)
            .unwrap()
            .into_rgba8()
            .get_pixel(0, 0)
            .0,
        [64, 128, 0, 128]
    );
    let mut rgb15 = vec![1, 0, 4, 1, 0, 1, 0];
    rgb15.extend(zlib(&[0, 0x7c, 0, 0]));
    let parsed = Movie::from_bytes(&movie(&[tag(20, &rgb15)]), "rgb15.swf").unwrap();
    let png = export_resource(&parsed, &parsed.tags[0], "png").unwrap();
    assert_eq!(
        image::load_from_memory(&png.bytes)
            .unwrap()
            .into_rgba8()
            .get_pixel(0, 0)
            .0,
        [255, 0, 0, 255]
    );
}

#[test]
fn corrupt_lossless_length_fails_instead_of_padding_pixels() {
    let mut payload = vec![1, 0, 5, 1, 0, 1, 0];
    payload.extend(zlib(&[255, 0, 0]));
    let parsed = Movie::from_bytes(&movie(&[tag(36, &payload)]), "broken.swf").unwrap();
    assert!(export_resource(&parsed, &parsed.tags[0], "png").is_err());
    assert_eq!(
        export_resource(&parsed, &parsed.tags[0], "raw")
            .unwrap()
            .bytes,
        payload
    );
}

#[test]
fn jpeg_alpha_plane_is_preserved() {
    let mut jpeg = Vec::new();
    image::codecs::jpeg::JpegEncoder::new(&mut jpeg)
        .encode(&[200, 100, 40], 1, 1, image::ExtendedColorType::Rgb8)
        .unwrap();
    let mut payload = vec![1, 0];
    payload.extend_from_slice(&(jpeg.len() as u32).to_le_bytes());
    payload.extend(jpeg);
    payload.extend(zlib(&[77]));
    let parsed = Movie::from_bytes(&movie(&[tag(35, &payload)]), "jpegalpha.swf").unwrap();
    let png = export_resource(&parsed, &parsed.tags[0], "png").unwrap();
    assert_eq!(
        image::load_from_memory(&png.bytes)
            .unwrap()
            .into_rgba8()
            .get_pixel(0, 0)
            .0[3],
        77
    );
}

#[test]
fn original_jpeg_exports_shared_tables_and_jpeg2_without_reencoding() {
    let mut jpeg = Vec::new();
    image::codecs::jpeg::JpegEncoder::new_with_quality(&mut jpeg, 93)
        .encode(
            &[200, 100, 40, 30, 60, 90],
            2,
            1,
            image::ExtendedColorType::Rgb8,
        )
        .unwrap();
    let mut tables = vec![0xff, 0xd8];
    let mut scan = vec![0xff, 0xd8];
    let mut position = 2;
    while position < jpeg.len() {
        assert_eq!(jpeg[position], 0xff);
        let marker = jpeg[position + 1];
        if marker == 0xda {
            scan.extend_from_slice(&jpeg[position..]);
            break;
        }
        let length = u16::from_be_bytes([jpeg[position + 2], jpeg[position + 3]]) as usize;
        let end = position + 2 + length;
        if matches!(marker, 0xdb | 0xc4) {
            tables.extend_from_slice(&jpeg[position..end]);
        } else {
            scan.extend_from_slice(&jpeg[position..end]);
        }
        position = end;
    }
    tables.extend_from_slice(&[0xff, 0xd9]);
    let mut define_bits = vec![1, 0];
    define_bits.extend_from_slice(&scan);
    let mut jpeg2 = vec![2, 0];
    jpeg2.extend_from_slice(&jpeg);
    let rgba = image::RgbaImage::from_raw(1, 1, vec![100, 40, 200, 80]).unwrap();
    let mut png = Vec::new();
    use image::ImageEncoder;
    image::codecs::png::PngEncoder::new(&mut png)
        .write_image(rgba.as_raw(), 1, 1, image::ExtendedColorType::Rgba8)
        .unwrap();
    let mut png2 = vec![3, 0];
    png2.extend(png);
    let parsed = Movie::from_bytes(
        &movie(&[
            tag(8, &tables),
            tag(6, &define_bits),
            tag(21, &jpeg2),
            tag(21, &png2),
            tag(1, &[]),
        ]),
        "original-images.swf",
    )
    .unwrap();
    let original = export_resource(&parsed, &parsed.tags[2], "jpeg").unwrap();
    assert_eq!(original.bytes, jpeg);
    assert_eq!(original.mime, "image/jpeg");
    assert_eq!(original.extension, "jpg");
    let mut expected = tables[..tables.len() - 2].to_vec();
    expected.extend_from_slice(&scan[2..]);
    let shared = export_resource(&parsed, &parsed.tags[1], "jpeg").unwrap();
    assert_eq!(shared.bytes, expected);
    assert_eq!(
        image::load_from_memory(&shared.bytes)
            .unwrap()
            .into_rgba8()
            .dimensions(),
        (2, 1)
    );
    assert!(supported_formats(&parsed, &parsed.tags[1]).contains(&"jpeg".into()));
    assert!(supported_formats(&parsed, &parsed.tags[2]).contains(&"jpeg".into()));
    assert!(!supported_formats(&parsed, &parsed.tags[3]).contains(&"jpeg".into()));
    assert!(export_resource(&parsed, &parsed.tags[3], "jpeg").is_err());
    let frame = resource_metadata(&parsed, &parsed.tags[4]);
    assert_eq!(frame["preview"], "not_available");
    assert!(frame["reason"].as_str().unwrap().contains("raw bytes"));
}

#[test]
fn pcm_mp3_and_adpcm_outputs_have_real_headers() {
    let mut pcm = vec![1, 0, 0x3e];
    pcm.extend_from_slice(&2u32.to_le_bytes());
    pcm.extend_from_slice(&[0, 0, 0xff, 0x7f]);
    let mut mp3 = vec![2, 0, 0x2e];
    mp3.extend_from_slice(&1u32.to_le_bytes());
    mp3.extend_from_slice(&[0, 0, 0xff, 0xfb, 0x90, 0x64]);
    let mut adpcm = vec![3, 0, 0x1e];
    adpcm.extend_from_slice(&2u32.to_le_bytes());
    adpcm.extend(bits(&[(0, 2), (0, 16), (0, 6), (1, 2)]));
    let parsed = Movie::from_bytes(
        &movie(&[tag(14, &pcm), tag(14, &mp3), tag(14, &adpcm)]),
        "sounds.swf",
    )
    .unwrap();
    let wav = export_resource(&parsed, &parsed.tags[0], "wav").unwrap();
    assert_eq!(&wav.bytes[..4], b"RIFF");
    assert_eq!(&wav.bytes[44..], [0, 0, 255, 127]);
    assert_eq!(
        export_resource(&parsed, &parsed.tags[1], "mp3")
            .unwrap()
            .bytes,
        [255, 251, 144, 100]
    );
    let wav = export_resource(&parsed, &parsed.tags[2], "wav").unwrap();
    assert_eq!(&wav.bytes[44..], [0, 0, 10, 0]);
}

#[test]
fn binary_export_strips_container_and_raw_keeps_it() {
    let data = [1, 0, 0, 0, 0, 0, 80, 75, 3, 4];
    let parsed = Movie::from_bytes(&movie(&[tag(87, &data)]), "binary.swf").unwrap();
    assert_eq!(
        export_resource(&parsed, &parsed.tags[0], "bin")
            .unwrap()
            .bytes,
        [80, 75, 3, 4]
    );
    assert_eq!(
        export_resource(&parsed, &parsed.tags[0], "raw")
            .unwrap()
            .bytes,
        data
    );
    assert!(supported_formats(&parsed, &parsed.tags[0]).contains(&"bin".into()));
    assert_eq!(
        resource_metadata(&parsed, &parsed.tags[0])["embedded_bytes"],
        4
    );
}

#[test]
fn real_shape_font_and_text_formats_export_paths_and_unicode() {
    use swf::*;
    let rectangle = Rectangle {
        x_min: Twips::ZERO,
        y_min: Twips::ZERO,
        x_max: Twips::new(1024),
        y_max: Twips::new(1024),
    };
    let records = vec![
        ShapeRecord::StyleChange(Box::new(StyleChangeData {
            move_to: Some(Point {
                x: Twips::ZERO,
                y: Twips::ZERO,
            }),
            fill_style_0: None,
            fill_style_1: Some(1),
            line_style: None,
            new_styles: None,
        })),
        ShapeRecord::StraightEdge {
            delta: PointDelta {
                dx: Twips::new(1024),
                dy: Twips::ZERO,
            },
        },
        ShapeRecord::StraightEdge {
            delta: PointDelta {
                dx: Twips::new(-1024),
                dy: Twips::new(1024),
            },
        },
        ShapeRecord::StraightEdge {
            delta: PointDelta {
                dx: Twips::ZERO,
                dy: Twips::new(-1024),
            },
        },
    ];
    let shape = Shape {
        version: 3,
        id: 1,
        shape_bounds: rectangle,
        edge_bounds: rectangle,
        flags: ShapeFlag::empty(),
        styles: ShapeStyles {
            fill_styles: vec![FillStyle::Color(Color {
                r: 255,
                g: 0,
                b: 0,
                a: 128,
            })],
            line_styles: vec![],
        },
        shape: records.clone(),
    };
    let font = Font {
        version: 2,
        id: 2,
        name: SwfStr::from_utf8_str("Fixture"),
        language: Language::Latin,
        layout: None,
        glyphs: vec![Glyph {
            shape_records: records,
            code: 0x00f1,
            advance: 1024,
            bounds: None,
        }],
        flags: FontFlag::HAS_WIDE_CODES,
    };
    let text = Text {
        id: 3,
        bounds: rectangle,
        matrix: Matrix::IDENTITY,
        records: vec![TextRecord {
            font_id: Some(2),
            color: Some(Color {
                r: 0,
                g: 0,
                b: 255,
                a: 255,
            }),
            x_offset: Some(Twips::ZERO),
            y_offset: Some(Twips::new(1000)),
            height: Some(Twips::new(500)),
            glyphs: vec![GlyphEntry {
                index: 0,
                advance: 500,
            }],
        }],
    };
    let header = Header {
        compression: Compression::None,
        version: 10,
        stage_size: rectangle,
        frame_rate: Fixed8::from_f32(24.0),
        num_frames: 1,
    };
    let mut bytes = Vec::new();
    write_swf(
        &header,
        &[
            Tag::DefineShape(Box::new(shape)),
            Tag::DefineFont2(Box::new(font)),
            Tag::DefineText2(Box::new(text)),
        ],
        &mut bytes,
    )
    .unwrap();
    let parsed = Movie::from_bytes(&bytes, "paths.swf").unwrap();
    let svg = export_resource(&parsed, &parsed.tags[0], "svg").unwrap();
    let svg = String::from_utf8(svg.bytes).unwrap();
    assert!(svg.contains("rgba(255,0,0,0.501"));
    assert!(svg.contains("<path"));
    assert!(svg.contains("Z "));
    let glyphs = String::from_utf8(
        export_resource(&parsed, &parsed.tags[1], "svg")
            .unwrap()
            .bytes,
    )
    .unwrap();
    assert!(glyphs.contains("<path"));
    assert_eq!(
        resource_metadata(&parsed, &parsed.tags[1])["glyph_count"],
        1
    );
    assert_eq!(
        String::from_utf8(
            export_resource(&parsed, &parsed.tags[2], "txt")
                .unwrap()
                .bytes
        )
        .unwrap(),
        "ñ"
    );
    let text = export_resource(&parsed, &parsed.tags[2], "svg").unwrap();
    assert!(text.diagnostics.is_empty());
    assert!(
        String::from_utf8(text.bytes)
            .unwrap()
            .contains("rgba(0,0,255,1)")
    );
}

#[test]
fn pinned_corpus_is_read_and_selected_resources_export() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let corpus = std::env::var_os("SWF_CORPUS_DIR").map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            let current = root.join("../../jpexs-decompiler-master/libsrc/ffdec_lib/testdata");
            if current.is_dir() { current } else {
                root.join("../../py_swf/third_party/references/jpexs-26.3.0/libsrc/ffdec_lib/testdata")
            }
        });
    assert!(
        corpus.is_dir(),
        "SWF corpus missing at {}. Set SWF_CORPUS_DIR to the JPEXS testdata directory; certification cannot silently skip it.",
        corpus.display()
    );
    let mut parsed_files = 0;
    let mut assets = 0;
    for name in ["as2/as2.swf", "as3/as3.swf", "as3_new/bin/as3_new.flex.swf"] {
        let path = corpus.join(name);
        assert!(
            path.is_file(),
            "Required corpus fixture missing: {}",
            path.display()
        );
        let data = std::fs::read(path).unwrap();
        let parsed = Movie::from_bytes(&data, name).unwrap();
        assert_eq!(parsed.original, data);
        assert!(
            parsed
                .tags
                .iter()
                .any(|t| matches!(t.code, 12 | 59 | 72 | 82))
        );
        for tag in parsed
            .tags
            .iter()
            .filter(|t| {
                matches!(
                    t.code,
                    2 | 22 | 32 | 83 | 20 | 21 | 35 | 36 | 48 | 75 | 11 | 33 | 14
                )
            })
            .take(20)
        {
            let formats = supported_formats(&parsed, tag);
            if let Some(format) = formats.iter().find(|f| f.as_str() != "raw") {
                let asset = export_resource(&parsed, tag, format)
                    .unwrap_or_else(|e| panic!("{name}: {} {}: {e}", tag.id, tag.name));
                assert!(!asset.bytes.is_empty());
                assets += 1;
            }
        }
        parsed_files += 1;
    }
    assert_eq!(parsed_files, 3);
    assert!(assets > 0);
}
