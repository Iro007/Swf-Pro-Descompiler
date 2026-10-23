#!/usr/bin/env node
'use strict';
/**
 * Writes desktop/build/icon.png (256x256), the application mark electron-builder turns into
 * the Windows icon. Code-generated, like tools/make-icon.mjs's 32 px .ico: no external
 * artwork. The mark is that same 32x32 drawing enlarged eight times.
 */
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const CELLS = 32;
const SCALE = 8;
const SIZE = CELLS * SCALE;

function colorAt(cx, cy) {
  const inside = cx >= 3 && cx < 29 && cy >= 3 && cy < 29;
  const bracket =
    (cx === 9 && cy >= 10 && cy <= 21) ||
    (cx === 22 && cy >= 10 && cy <= 21) ||
    ((cy === 10 || cy === 21) && ((cx >= 9 && cx <= 12) || (cx >= 19 && cx <= 22)));
  const slash = cx === Math.round(20 - (cy - 9) * 0.55) && cy >= 9 && cy <= 22;
  if (bracket || slash) return [15, 41, 34, 255];
  return inside ? [98, 218, 177, 255] : [11, 16, 24, 255];
}

function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, 'latin1');
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlib.crc32(Buffer.concat([head.subarray(4), data])) >>> 0, 0);
  return Buffer.concat([head, data, crc]);
}

function png() {
  const rows = Buffer.alloc((SIZE * 4 + 1) * SIZE);
  for (let y = 0; y < SIZE; y += 1) {
    const base = y * (SIZE * 4 + 1);
    rows[base] = 0;
    for (let x = 0; x < SIZE; x += 1) {
      const [r, g, b, a] = colorAt(Math.floor(x / SCALE), Math.floor(y / SCALE));
      rows.set([r, g, b, a], base + 1 + x * 4);
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(SIZE, 0);
  header.writeUInt32BE(SIZE, 4);
  header.set([8, 6, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(rows, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

if (require.main === module) {
  const target = path.join(__dirname, '..', 'build', 'icon.png');
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, png());
  console.log(`icono: ${target}`);
}

module.exports = { png, SIZE };
