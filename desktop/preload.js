'use strict';
/**
 * The only door between the page and the application: `window.flash`. The renderer
 * never sees Node, Electron or a file system; it asks the main process (which owns the
 * harness) through these few functions, and every request is validated there again.
 * Sandboxed preload: only `electron` can be required here.
 *
 *   flash.call(op, args?)        run a protocol or native operation; resolves with the envelope
 *                                { ok, op, session_id, revision, sha256, served_by, via?, attempts,
 *                                  changed, elapsed_ms, result }  or  { ok:false, error:{message,kind,attempts?} }
 *   flash.cancel()               stop what is running; the document survives
 *   flash.engines()              engines with availability, capability levels L1..L5 and run state
 *   flash.openDialog(kind)       file / folder / output picker -> path or null
 *   flash.saveDialog(options?)   "save as" for an exported item -> path or null
 *   flash.assetUrl(path)         URL the page can use for a file the engines extracted
 *   flash.startup()              { path, smoke, packaged, version, ... } at launch (--open)
 *   flash.onMenu(callback)       native menu commands -> unsubscribe function
 *   flash.onEngineEvent(callback) engine start / exit notifications -> unsubscribe function
 *   flash.onFileDrop(callback)   the path of a file dropped on the window -> unsubscribe function
 *   flash.reportVerification(evidence)  what a verification run (--smoke-report) saw in the interface
 *
 * ui/src/api.ts (the only module of the interface that talks to its host) maps onto this one
 * to one: native -> `window.flash` exists, suiteRpc -> call (and unwrap `result`), suitePick ->
 * openDialog, suiteCancel -> cancel, startup -> startup, reportVerification and onFileDrop -> the
 * functions of the same name.
 */
const { contextBridge, ipcRenderer, webUtils } = require('electron');

const subscribe = (channel) => (callback) => {
  if (typeof callback !== 'function') throw new TypeError('Se esperaba una función');
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
};

const carriesFiles = (event) => Boolean(event.dataTransfer && Array.from(event.dataTransfer.types || []).includes('Files'));

/**
 * Files dropped on the window. The page never receives a `File`: only the path of the first
 * one, a string. A file that is not on disk has no path and is ignored.
 */
function fileDrop(callback) {
  if (typeof callback !== 'function') throw new TypeError('Se esperaba una función');
  const over = (event) => {
    if (carriesFiles(event)) event.preventDefault();
  };
  const drop = (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    const file = event.dataTransfer.files[0];
    const filePath = file ? webUtils.getPathForFile(file) : '';
    if (typeof filePath === 'string' && filePath !== '') callback(filePath);
  };
  window.addEventListener('dragover', over);
  window.addEventListener('drop', drop);
  return () => {
    window.removeEventListener('dragover', over);
    window.removeEventListener('drop', drop);
  };
}

const flash = {
  api: 1,
  call: (op, args) => ipcRenderer.invoke('flash:call', { op, args: args === undefined ? {} : args }),
  cancel: () => ipcRenderer.invoke('flash:cancel'),
  engines: () => ipcRenderer.invoke('flash:engines'),
  openDialog: (kind) => ipcRenderer.invoke('flash:dialog:open', { kind }),
  saveDialog: (options) => ipcRenderer.invoke('flash:dialog:save', options === undefined ? {} : options),
  assetUrl: (file) => `flash-asset://local/?p=${encodeURIComponent(String(file))}`,
  startup: () => ipcRenderer.invoke('flash:startup'),
  reportVerification: (evidence) => ipcRenderer.invoke('flash:verified', { evidence }),
  onMenu: subscribe('flash:menu'),
  onEngineEvent: subscribe('flash:engine-event'),
  onFileDrop: fileDrop,
};

contextBridge.exposeInMainWorld('flash', Object.freeze(flash));
