'use strict';
/**
 * `flash-asset://` serves the files the engines extract (images, sounds, SVG) to the
 * renderer. The URL names a local path; the main process serves it only when the real
 * path (links resolved) lies inside a folder the harness owns. Pure helpers, testable
 * without Electron; the protocol handler is in main.js.
 */
const fs = require('node:fs');
const path = require('node:path');

const SCHEME = 'flash-asset';
const HOST = 'local';

/** What preload.js builds for `assetUrl(path)`. */
function assetUrl(file) {
  return `${SCHEME}://${HOST}/?p=${encodeURIComponent(String(file))}`;
}

/** The path a flash-asset URL names, or null when the URL is not one of ours. */
function parseAssetUrl(text) {
  let url;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== `${SCHEME}:` || url.hostname !== HOST) return null;
  const file = url.searchParams.get('p');
  return file && file.length < 4096 && !file.includes('\0') ? file : null;
}

/** Whether `target` is `root` or lies below it, comparing real paths (case-insensitively on Windows). */
function isWithin(root, target) {
  const norm = (value) => (process.platform === 'win32' ? value.toLowerCase() : value);
  const relative = path.relative(norm(root), norm(target));
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

/**
 * The real file to serve for `requested`, or null: it must exist, be a regular file, and
 * lie inside one of `roots` once symbolic links and junctions are resolved.
 */
function resolveAsset(requested, roots) {
  if (typeof requested !== 'string' || !path.isAbsolute(requested)) return null;
  let real;
  try {
    real = fs.realpathSync(requested);
    if (!fs.statSync(real).isFile()) return null;
  } catch {
    return null;
  }
  for (const root of roots) {
    let realRoot;
    try {
      realRoot = fs.realpathSync(root);
    } catch {
      continue;
    }
    if (isWithin(realRoot, real)) return real;
  }
  return null;
}

module.exports = { SCHEME, HOST, assetUrl, parseAssetUrl, isWithin, resolveAsset };
