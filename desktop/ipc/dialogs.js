'use strict';
/**
 * The file pickers of the application, kind by kind (the same kinds the previous desktop host's
 * `suite_pick` had). Pure option builders; main.js hands them to Electron's `dialog`.
 */
const path = require('node:path');

const FILTERS = {
  swf: [{ name: 'Flash SWF', extensions: ['swf'] }],
  project: [{ name: 'Proyecto Flash Suite', extensions: ['pyswfproj'] }],
  source: [{ name: 'ActionScript / Haxe / ensamblado', extensions: ['as', 'mxml', 'hx', 'hxml', 'xml', 'asasm', 'flm', 'abc'] }],
  image: [{ name: 'Imagen', extensions: ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'] }],
  audio: [{ name: 'Audio', extensions: ['mp3', 'wav'] }],
  tool: [{ name: 'Programa', extensions: ['exe', 'jar', 'bat', 'cmd'] }],
};

/** Kinds that choose an existing file or folder. */
const OPEN_KINDS = ['swf', 'project', 'source', 'image', 'audio', 'tool', 'folder'];
/** Kinds that choose where to write. */
const SAVE_KINDS = ['output', 'project_output'];
/** Everything `openDialog(kind)` accepts, like the old `suite_pick`. */
const KINDS = [...OPEN_KINDS, ...SAVE_KINDS];

const TITLES = {
  swf: 'Abrir SWF',
  project: 'Abrir proyecto',
  source: 'Elegir fuente',
  image: 'Elegir imagen',
  audio: 'Elegir audio',
  tool: 'Elegir programa',
  folder: 'Elegir carpeta',
  output: 'Guardar SWF',
  project_output: 'Guardar proyecto',
};

function openOptions(kind) {
  if (kind === 'folder') return { title: TITLES.folder, properties: ['openDirectory', 'createDirectory'] };
  return { title: TITLES[kind], properties: ['openFile'], filters: FILTERS[kind] };
}

function saveOptions(kind, defaultDirectory) {
  const name = kind === 'project_output' ? 'project.pyswfproj' : 'compiled.swf';
  return {
    title: TITLES[kind],
    defaultPath: defaultDirectory ? path.join(defaultDirectory, name) : name,
    filters: kind === 'project_output' ? FILTERS.project : FILTERS.swf,
  };
}

/** Save dialog for an exported item: the name the application suggests, filtered by its extensions. */
function exportOptions({ name, extensions }, defaultDirectory) {
  const filters = extensions.length > 0 ? [{ name: extensions.join(', '), extensions }, { name: 'Todos', extensions: ['*'] }] : [{ name: 'Todos', extensions: ['*'] }];
  return { title: 'Guardar', defaultPath: defaultDirectory ? path.join(defaultDirectory, name) : name, filters };
}

module.exports = { FILTERS, OPEN_KINDS, SAVE_KINDS, KINDS, openOptions, saveOptions, exportOptions };
