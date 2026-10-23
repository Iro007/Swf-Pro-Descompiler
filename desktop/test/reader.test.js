'use strict';
/**
 * The harness's own SWF reader (swfheader.js) and the last-resort `header` and `raw`
 * answers built on it (builtin.js). They answer in the shapes every engine uses.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parseHeader, scanTags, headerTags, tagName, TAG_NAMES, CHARACTER_TAGS } = require('../harness/swfheader');
const { Protocol } = require('../harness/protocol');
const builtin = require('../harness/builtin');
const { swfBytes, tagBytes, defaultSwf } = require('../testkit');

const protocol = Protocol.load([path.resolve(__dirname, '..', '..')]);

function movie() {
  const sprite = [2, 0, 2, 0, ...tagBytes(26, [0, 1, 0, 1, 0]), ...tagBytes(1), ...tagBytes(26, [0, 1, 0, 1, 0]), ...tagBytes(1), ...tagBytes(0)];
  const symbols = Buffer.concat([Buffer.from([1, 0, 2, 0]), Buffer.from('Hero\0')]);
  return swfBytes([
    tagBytes(69, [0x08, 0, 0, 0]),
    tagBytes(2, [1, 0, 0, 0, 0, 0]),
    tagBytes(39, sprite),
    tagBytes(11, [3, 0, 0, 0]),
    tagBytes(21, [4, 0, 0xff, 0xd8]),
    tagBytes(14, [5, 0, 0, 0, 0, 0, 0, 0, 0]),
    tagBytes(34, [6, 0, 0, 0, 0]),
    tagBytes(48, [7, 0, 0, 0]),
    tagBytes(87, [8, 0, 0, 0, 0, 0]),
    tagBytes(60, [9, 0, 0, 0]),
    tagBytes(46, [10, 0, 0, 0]),
    tagBytes(76, symbols),
    tagBytes(26, [0, 1, 0, 1, 0]),
    tagBytes(43, Buffer.from('intro\0')),
    tagBytes(12, [0]),
    tagBytes(1),
    tagBytes(26, [0, 1, 0, 1, 0]),
    tagBytes(12, [0]),
    tagBytes(1),
  ]);
}

test('parseHeader reads the stage, rate and frame count of FWS and CWS, and the little it can of ZWS', () => {
  const fws = swfBytes([tagBytes(1)], { version: 13, frameCount: 7 });
  const header = parseHeader(fws);
  assert.deepEqual(
    [header.signature, header.compression, header.version, header.file_length, header.file_size, header.frame_rate, header.frame_count],
    ['FWS', 'none', 13, fws.length, fws.length, 24, 7],
  );
  assert.deepEqual(header.frame_size, { xmin: 0, xmax: 2000, ymin: 0, ymax: 1000, width: 100, height: 50 });
  const cws = parseHeader(swfBytes([tagBytes(1)], { signature: 'CWS' }));
  assert.deepEqual([cws.signature, cws.compression, cws.frame_size.width], ['CWS', 'zlib', 100]);
  const zws = parseHeader(Buffer.concat([Buffer.from('ZWS\x0d', 'latin1'), Buffer.from([64, 0, 0, 0]), Buffer.alloc(40)]));
  assert.deepEqual([zws.signature, zws.compression, zws.frame_size, zws.frame_rate], ['ZWS', 'lzma', null, null]);
  assert.match(zws.partial, /LZMA/);
});

test('parseHeader and scanTags reject what is not a SWF, truncated files and tags that overrun', () => {
  assert.throws(() => parseHeader(Buffer.from('nope')), /truncado/);
  assert.throws(() => parseHeader(Buffer.from('ABC\x09\0\0\0\0')), /Firma/);
  assert.throws(() => parseHeader('text'), /truncado/);
  const bad = swfBytes([]);
  bad.writeUInt16LE((2 << 6) | 63, bad.length - 2); // the End tag becomes a long tag with no room for its length
  assert.throws(() => scanTags(bad), (e) => e.kind === 'failed');
  const overrun = Buffer.from(swfBytes([tagBytes(2, [1, 0, 0, 0])]));
  overrun.writeUInt16LE((2 << 6) | 40, overrun.length - 8); // claims 40 payload bytes
  assert.throws(() => scanTags(overrun), /excede su contenedor/);
  assert.throws(() => scanTags(Buffer.concat([Buffer.from('ZWS\x0d', 'latin1'), Buffer.alloc(40)])), (e) => e.kind === 'unsupported');
});

test('scanTags lists every tag in document order with nested sprite content, ids, symbols and labels', () => {
  const { tags } = scanTags(movie());
  assert.equal(tags.length, 19 + 1 + 5, 'top level + End, plus the sprite\'s five inner tags');
  const names = tags.filter((t) => t.path.length === 1).map((t) => t.name);
  assert.deepEqual(names.slice(0, 4), ['FileAttributes', 'DefineShape', 'DefineSprite', 'DefineText']);
  const inner = tags.filter((t) => t.path.length === 2);
  assert.deepEqual(inner.map((t) => [t.path.join('/'), t.name]), [['2/0', 'PlaceObject2'], ['2/1', 'ShowFrame'], ['2/2', 'PlaceObject2'], ['2/3', 'ShowFrame'], ['2/4', 'End']]);
  const sprite = tags.find((t) => t.name === 'DefineSprite');
  assert.deepEqual([sprite.character_id, sprite.symbol], [2, 'Hero']);
  assert.equal(tags.find((t) => t.name === 'FrameLabel').label_text, 'intro');
  assert.equal(tags.find((t) => t.name === 'FileAttributes').character_id, null);
  assert.equal(tags.at(-1).name, 'End');
});

test('scanTags stops at End, handles long tags, unknown codes and refuses runaway nesting', () => {
  const big = Buffer.alloc(300, 7);
  const tags = scanTags(swfBytes([tagBytes(87, Buffer.concat([Buffer.from([1, 0, 0, 0, 0, 0]), big])), tagBytes(511, [1, 2, 3])])).tags;
  assert.deepEqual(tags.map((t) => [t.name, t.length]), [['DefineBinaryData', 306], ['Unknown_511', 3], ['End', 0]]);
  const trailing = Buffer.concat([swfBytes([tagBytes(1)]), Buffer.from([1, 2, 3])]);
  assert.equal(scanTags(trailing).tags.at(-1).name, 'End', 'bytes after End are ignored');
  let nested = tagBytes(1);
  for (let depth = 0; depth < 70; depth += 1) nested = tagBytes(39, Buffer.concat([Buffer.from([depth + 1, 0, 1, 0]), nested, tagBytes(0)]));
  assert.throws(() => scanTags(swfBytes([nested])), (e) => e.kind === 'limit' && /64/.test(e.message));
});

test('headerTags reads attributes, background color, script limits and metadata; counts are the main timeline\'s', () => {
  const xml = Buffer.from('<x/>\0trailing');
  const bytes = swfBytes([
    tagBytes(69, [0x58, 0, 0, 0]),
    tagBytes(9, [0xff, 0x80, 0x00]),
    tagBytes(65, [0xe8, 3, 15, 0]),
    tagBytes(77, xml),
    tagBytes(1),
  ]);
  const info = headerTags(scanTags(bytes));
  assert.deepEqual([info.tag_count, info.shown_frames, info.abc_blocks], [6, 1, 0]);
  assert.equal(info.background_color, '#ff8000');
  assert.deepEqual(info.file_attributes, { use_network: false, actionscript3: true, has_metadata: true, use_gpu: false, use_direct_blit: true });
  assert.deepEqual(info.script_limits, { max_recursion_depth: 1000, script_timeout_seconds: 15 });
  assert.deepEqual([info.has_as3, info.has_metadata, info.metadata], [true, true, '<x/>']);
  const bare = headerTags(scanTags(swfBytes([tagBytes(1)])));
  assert.deepEqual([bare.file_attributes, bare.script_limits, bare.metadata, bare.has_as3, bare.has_metadata], [null, null, null, false, false]);
  // Sprite contents are not counted, ABC code makes a movie AS3 without FileAttributes, and the
  // FileAttributes flag alone announces metadata.
  const sprite = [2, 0, 2, 0, ...tagBytes(1), ...tagBytes(1), ...tagBytes(0)];
  const counted = headerTags(scanTags(swfBytes([tagBytes(39, sprite), tagBytes(82, [0, 0, 0, 0, 97, 0]), tagBytes(72, [1]), tagBytes(1), tagBytes(1)])));
  assert.deepEqual([counted.tag_count, counted.shown_frames, counted.abc_blocks, counted.has_as3], [6, 2, 2, true]);
  const flagged = headerTags(scanTags(swfBytes([tagBytes(69, [0x10, 0, 0, 0])])));
  assert.deepEqual([flagged.has_metadata, flagged.metadata, flagged.has_as3], [true, null, false]);
  assert.equal(headerTags(scanTags(defaultSwf())).script_limits, null);
});

test('tag names cover every code the protocol files into a folder', () => {
  for (const codes of Object.values(protocol.tagFolders.by_code)) for (const code of codes) assert.ok(TAG_NAMES[code], `no name for ${code}`);
  for (const code of protocol.tagFolders.frame_control) assert.ok(TAG_NAMES[code], `no name for control tag ${code}`);
  for (const code of CHARACTER_TAGS) assert.ok(TAG_NAMES[code], `no name for character tag ${code}`);
  assert.equal(tagName(82), 'DoABC');
  assert.equal(tagName(72), 'DoABC1');
  assert.equal(tagName(404), 'Unknown_404');
  assert.equal(new Set(Object.values(TAG_NAMES)).size, Object.keys(TAG_NAMES).length, 'names are unique');
});

test('the tag names are the table PySWF uses, so a tag reads the same whichever engine lists it', () => {
  const source = fs.readFileSync(path.resolve(__dirname, '..', '..', 'engines', 'pyswf', 'py_swf', 'swf_parser.py'), 'utf8');
  const table = source.slice(source.indexOf('TAG_NAMES = {'), source.indexOf('def collect_symbol_names'));
  const pyswf = Object.fromEntries([...table.matchAll(/(\d+): "([A-Za-z0-9_]+)"/g)].map((m) => [Number(m[1]), m[2]]));
  assert.ok(Object.keys(pyswf).length > 60, 'the table was found');
  assert.deepEqual(TAG_NAMES, pyswf);
});

/** The built-in readers work on a working copy; this one lives in a throwaway file. */
function withWorkingCopy(bytes, body) {
  const file = path.join(os.tmpdir(), `harness-builtin-${process.pid}-${Math.random().toString(16).slice(2)}.swf`);
  fs.writeFileSync(file, bytes);
  try {
    return body({ workingPath: file, sourcePath: path.join('D:\\juegos', 'game.swf'), sha256: 'cafe' });
  } finally {
    fs.rmSync(file, { force: true });
  }
}

test('built-in header answers in the shape every engine uses', () => {
  withWorkingCopy(movie(), (document) => {
    const header = builtin.header(document);
    assert.deepEqual([header.filename, header.signature, header.compression, header.version], ['game.swf', 'FWS', 'none', 9]);
    assert.deepEqual(header.frame_size, { xmin: 0, xmax: 2000, ymin: 0, ymax: 1000, width: 100, height: 50 });
    assert.deepEqual([header.frame_rate, header.frame_count, header.tag_count, header.shown_frames, header.abc_blocks], [24, 1, 20, 2, 0]);
    assert.deepEqual([header.has_as3, header.has_metadata, header.file_attributes.actionscript3], [true, false, true]);
    assert.deepEqual([header.derived, header.revision, header.sha256.length], [true, header.sha256, 64]);
    assert.equal('script_count' in header, false, 'the harness does not know the code units');
    assert.throws(() => builtin.header(null), (e) => e.kind === 'not_open');
  });
  const lzma = Buffer.concat([Buffer.from('ZWS', 'latin1'), Buffer.from([13, 100, 0, 0, 0]), Buffer.alloc(32)]);
  withWorkingCopy(lzma, (document) => {
    const partial = builtin.header(document);
    assert.deepEqual([partial.signature, partial.frame_size, partial.tag_count, partial.diagnostics[0].code], ['ZWS', null, null, 'header_partial']);
  });
});

test('built-in raw pages a tag or the file, as hex or base64, and refuses bad requests with a reason', () => {
  withWorkingCopy(movie(), (document) => {
    const nested = builtin.raw(document, { tag_path: [2, 0], limit: 8 });
    assert.deepEqual([nested.scope, nested.name, nested.type, nested.tag_index, nested.tag_path], ['tag', 'PlaceObject2', 26, 2, [2, 0]]);
    assert.deepEqual([nested.total, nested.length, nested.offset, nested.limit, nested.encoding, nested.hex], [5, 5, 0, 8, 'hex', '0001000100']);
    assert.equal(nested.revision, 'cafe');
    const top = builtin.raw(document, { tag_index: 0 });
    assert.deepEqual([top.tag_path, top.limit, top.hex], [undefined, 4096, '08000000']);
    const byId = builtin.raw(document, { id: 'tag:13', offset: 0, limit: 5, encoding: 'base64' });
    assert.deepEqual([byId.name, byId.base64, byId.hex], ['FrameLabel', Buffer.from('intro').toString('base64'), undefined]);
    const slice = builtin.raw(document, { tag_index: 0, offset: 1, limit: 2 });
    assert.deepEqual([slice.hex, slice.length, slice.total], ['0000', 2, 4]);
    assert.equal(builtin.raw(document, { tag_index: 0, offset: 4 }).length, 0, 'a page at the end is empty');
    const whole = builtin.raw(document, { scope: 'file', limit: 8 });
    assert.deepEqual([whole.scope, whole.name, whole.length, whole.hex], ['file', 'Archivo SWF', 8, fs.readFileSync(document.workingPath).subarray(0, 8).toString('hex')]);
    assert.equal(whole.total, fs.statSync(document.workingPath).size);
    for (const args of [{ tag_index: 0, limit: 0 }, { tag_index: 0, limit: 65537 }, { tag_index: 0, limit: 1.5 }, { tag_index: 0, offset: -1 }, { tag_index: 0, offset: 5 }, { tag_index: 0, encoding: 'utf8' }, { id: 'nope' }, {}]) {
      assert.throws(() => builtin.raw(document, args), (e) => e.kind === 'bad_request', JSON.stringify(args));
    }
    assert.throws(() => builtin.raw(document, { tag_path: [99] }), (e) => e.kind === 'not_found');
    assert.throws(() => builtin.run('teleport', {}, document), (e) => e.kind === 'unsupported');
    assert.throws(() => builtin.raw(null, { tag_index: 0 }), (e) => e.kind === 'not_open');
  });
});
