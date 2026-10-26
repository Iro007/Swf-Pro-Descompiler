'use strict';
/**
 * Main process: the harness. It owns the engines (each one a JSONL worker process),
 * the open document and the routing between them; the window is only a view. The page
 * talks to it through preload.js's `window.flash` and nothing else.
 *
 * Environment: ELECTRON_DEV_URL=http://127.0.0.1:1420 loads the Vite dev server instead of
 * the built UI; FLASH_ENGINE_ROOTS adds folders searched for engines (path-list);
 * FLASH_DEVTOOLS=1 enables DevTools in a packaged build; FLASH_SHELL=1 shows the harness console even
 * when the interface is built; FLASH_UI_ROOT=<folder> serves a build of the interface from there.
 * Arguments: --open <file.swf>,
 * --smoke-report <file.json> (load, exercise window.flash, write the report, quit; a built
 * interface is also waited for: it reports what it saw through reportVerification),
 * --smoke-shot <file.png> (with the report: also save a screenshot of the window).
 */
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { app, BrowserWindow, Menu, dialog, ipcMain, protocol, net, session } = require('electron');
const { createHarness } = require('./harness');
const { asHarnessError } = require('./harness/errors');
const { validateCall, validateKind, validateSaveOptions, validateEvidence } = require('./ipc/validate');
const dialogs = require('./ipc/dialogs');
const assets = require('./ipc/assets');
const { buildMenuTemplate } = require('./ipc/menu');

const APP_SCHEME = 'app';
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' ${assets.SCHEME}: blob: data:`,
  `media-src 'self' ${assets.SCHEME}: blob:`,
  `connect-src 'self' ${assets.SCHEME}:`,
  "font-src 'self' data:",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

// Must run before `ready`.
protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true } },
  { scheme: assets.SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true } },
]);

function parseArgs(argv) {
  const args = { open: null, smokeReport: null, smokeShot: null };
  const rest = argv.slice(app.isPackaged ? 1 : 2);
  for (let i = 0; i < rest.length; i += 1) {
    if (rest[i] === '--open' && rest[i + 1]) args.open = path.resolve(rest[(i += 1)]);
    else if (rest[i] === '--smoke-report' && rest[i + 1]) args.smokeReport = path.resolve(rest[(i += 1)]);
    else if (rest[i] === '--smoke-shot' && rest[i + 1]) args.smokeShot = path.resolve(rest[(i += 1)]);
    else if (/\.(swf|pyswfproj|flashproj)$/i.test(rest[i]) && !args.open) args.open = path.resolve(rest[i]);
  }
  return args;
}

const cli = parseArgs(process.argv);
const devUrl = process.env.ELECTRON_DEV_URL || null;
const resourcesRoot = app.isPackaged ? process.resourcesPath : null;

function resolveBenchDir() {
  if (process.env.FLASH_BENCH_DIR) return process.env.FLASH_BENCH_DIR;
  const candidates = [];
  const addCandidate = (p) => {
    if (!p) return;
    let curr = path.resolve(p);
    while (curr) {
      const candidate = path.join(curr, 'reports', 'bench');
      if (fs.existsSync(path.join(candidate, 'index.json')) && !candidates.includes(candidate)) {
        candidates.push(candidate);
      }
      const parent = path.dirname(curr);
      if (parent === curr) break;
      curr = parent;
    }
  };
  addCandidate(process.env.PORTABLE_EXECUTABLE_DIR);
  addCandidate(process.cwd());
  addCandidate(path.resolve(__dirname, '..'));
  if (resourcesRoot) {
    const resCandidate = path.join(resourcesRoot, 'reports', 'bench');
    if (fs.existsSync(path.join(resCandidate, 'index.json')) && !candidates.includes(resCandidate)) {
      candidates.push(resCandidate);
    }
  }
  if (!candidates.length) return null;
  if (candidates.length === 1) return candidates[0];
  candidates.sort((a, b) => {
    try {
      const mtimeA = fs.statSync(path.join(a, 'index.json')).mtimeMs;
      const mtimeB = fs.statSync(path.join(b, 'index.json')).mtimeMs;
      return mtimeB - mtimeA;
    } catch {
      return 0;
    }
  });
  return candidates[0];
}

const discoveredBenchDir = resolveBenchDir();
if (discoveredBenchDir && !process.env.FLASH_BENCH_DIR) {
  process.env.FLASH_BENCH_DIR = discoveredBenchDir;
}
// FLASH_UI_ROOT serves a build of the interface from another folder (to try a build without installing it).
const uiRoot = process.env.FLASH_UI_ROOT
  ? path.resolve(process.env.FLASH_UI_ROOT)
  : app.isPackaged
    ? path.join(process.resourcesPath, 'ui')
    : path.join(__dirname, '..', 'ui', 'dist');
const shellRoot = path.join(__dirname, 'shell');

let harness = null;
let mainWindow = null;
let quitting = false;
/** What the interface reported through `reportVerification` during a --smoke-report run. */
let verification = null;
const verificationWaiters = [];

const log = (...parts) => console.log('[flash]', ...parts);

/** `scheme://host` of a URL. (`URL.origin` is "null" for custom schemes in Node, so it is built by hand.) */
const originOf = (text) => {
  const url = new URL(text);
  return `${url.protocol}//${url.host}`;
};

function allowedOrigins() {
  const origins = new Set([`${APP_SCHEME}://ui`, `${APP_SCHEME}://shell`]);
  if (devUrl) origins.add(originOf(devUrl));
  return origins;
}

/** Only the application's own window may call the main process. */
function assertSender(event) {
  const frame = event.senderFrame;
  const ok = mainWindow && event.sender === mainWindow.webContents && frame && allowedOrigins().has(originOf(frame.url));
  if (!ok) throw new Error('Origen no autorizado');
}

function serveFrom(root, requestUrl) {
  const url = new URL(requestUrl);
  let relative = decodeURIComponent(url.pathname);
  if (relative.endsWith('/') || relative === '') relative += 'index.html';
  const candidate = path.normalize(path.join(root, relative));
  const inside = path.relative(root, candidate);
  if (inside.startsWith('..') || path.isAbsolute(inside)) return null;
  if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  // A single-page application: unknown paths without an extension are routes.
  if (path.extname(candidate) === '' && fs.existsSync(path.join(root, 'index.html'))) return path.join(root, 'index.html');
  return null;
}

function registerProtocols() {
  protocol.handle(APP_SCHEME, async (request) => {
    const url = new URL(request.url);
    const root = url.hostname === 'shell' ? shellRoot : url.hostname === 'ui' ? uiRoot : null;
    const file = root ? serveFrom(root, request.url) : null;
    if (!file) return new Response('No encontrado', { status: 404 });
    const response = await net.fetch(pathToFileURL(file).toString());
    const headers = new Headers(response.headers);
    headers.set('Content-Security-Policy', CSP);
    headers.set('X-Content-Type-Options', 'nosniff');
    return new Response(response.body, { status: response.status, headers });
  });
  protocol.handle(assets.SCHEME, async (request) => {
    const requested = assets.parseAssetUrl(request.url);
    const roots = [harness.stateRoot, path.join(app.getPath('temp'), 'flash-suite')];
    const file = requested && assets.resolveAsset(requested, roots);
    if (!file) return new Response('No autorizado', { status: 403 });
    const response = await net.fetch(pathToFileURL(file).toString());
    const headers = new Headers(response.headers);
    headers.set('Cache-Control', 'no-store');
    return new Response(response.body, { status: response.status, headers });
  });
}

function hardenSession() {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);
  app.on('web-contents-created', (_event, contents) => {
    contents.setWindowOpenHandler(() => ({ action: 'deny' }));
    contents.on('will-attach-webview', (event) => event.preventDefault());
    contents.on('will-navigate', (event, url) => {
      if (!allowedOrigins().has(originOf(url))) event.preventDefault();
    });
  });
}

function registerIpc() {
  const reply = async (fn) => {
    try {
      return await fn();
    } catch (raw) {
      const error = asHarnessError(raw);
      return { ok: false, error: error.toJSON() };
    }
  };
  ipcMain.handle('flash:call', (event, payload) =>
    reply(async () => {
      assertSender(event);
      const { op, args } = validateCall(payload, harness.registry.allowedOps());
      return harness.request(op, args);
    }),
  );
  ipcMain.handle('flash:cancel', (event) =>
    reply(async () => {
      assertSender(event);
      harness.cancel();
      return { ok: true };
    }),
  );
  ipcMain.handle('flash:engines', (event) =>
    reply(async () => {
      assertSender(event);
      return harness.listEngines();
    }),
  );
  ipcMain.handle('flash:startup', (event) =>
    reply(async () => {
      assertSender(event);
      return {
        path: cli.open,
        smoke: Boolean(cli.smokeReport),
        packaged: app.isPackaged,
        version: app.getVersion(),
        electron: process.versions.electron,
        node: process.versions.node,
        platform: process.platform,
        ui: devUrl ? 'dev' : fs.existsSync(path.join(uiRoot, 'index.html')) ? 'built' : 'shell',
        problems: harness.registry.problems,
      };
    }),
  );
  ipcMain.handle('flash:verified', (event, payload) =>
    reply(async () => {
      assertSender(event);
      const evidence = validateEvidence(payload);
      // Only a verification run listens; in normal use the report is simply not wanted.
      if (cli.smokeReport) {
        verification = evidence;
        for (const wake of verificationWaiters.splice(0)) wake(evidence);
      }
      return { ok: true };
    }),
  );
  ipcMain.handle('flash:dialog:open', (event, payload) =>
    reply(async () => {
      assertSender(event);
      const kind = validateKind(payload && payload.kind, dialogs.KINDS);
      const options = dialogs.SAVE_KINDS.includes(kind) ? dialogs.saveOptions(kind) : dialogs.openOptions(kind);
      const chosen = dialogs.SAVE_KINDS.includes(kind)
        ? await dialog.showSaveDialog(mainWindow, options)
        : await dialog.showOpenDialog(mainWindow, options);
      if (chosen.canceled) return null;
      return chosen.filePath || (chosen.filePaths && chosen.filePaths[0]) || null;
    }),
  );
  ipcMain.handle('flash:dialog:save', (event, payload) =>
    reply(async () => {
      assertSender(event);
      const options = dialogs.exportOptions(validateSaveOptions(payload));
      const chosen = await dialog.showSaveDialog(mainWindow, options);
      return chosen.canceled ? null : chosen.filePath;
    }),
  );
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 880,
    minWidth: 960,
    minHeight: 640,
    title: 'Flash Universal Studio',
    backgroundColor: '#0b1018',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
      // A verification run drops a file on the window through the DevTools protocol, which needs them on.
      devTools: !app.isPackaged || process.env.FLASH_DEVTOOLS === '1' || Boolean(cli.smokeReport),
    },
  });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
  const built = fs.existsSync(path.join(uiRoot, 'index.html'));
  const target = devUrl || `${APP_SCHEME}://${built && process.env.FLASH_SHELL !== '1' ? 'ui' : 'shell'}/index.html`;
  log('cargando', target);
  mainWindow.loadURL(target);
  return mainWindow;
}

/** Whether the window shows a built interface (or the dev server) rather than the harness console. */
const realInterface = () => Boolean(devUrl) || (fs.existsSync(path.join(uiRoot, 'index.html')) && process.env.FLASH_SHELL !== '1');

function waitForVerification(ms) {
  if (verification) return Promise.resolve(verification);
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), ms);
    verificationWaiters.push((evidence) => {
      clearTimeout(timer);
      resolve(evidence);
    });
  });
}

async function until(check, ms) {
  const end = Date.now() + ms;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) return null;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

/**
 * Drop a real file on the window the way the system does (DevTools protocol) and see which
 * path the page's handler gets. The page only ever receives a path string.
 */
async function simulateDrop(contents, file) {
  const probe = `window.__dropped = null; window.__stopDrop = window.flash.onFileDrop((p) => { window.__dropped = p; }); typeof window.__stopDrop`;
  const result = { expected: file, listener: await contents.executeJavaScript(probe, true) };
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  try {
    contents.debugger.attach('1.3');
    // The page decides whether a drop is accepted from its answer to dragover, so give it a few.
    for (let attempt = 0; attempt < 3 && !result.dropped; attempt += 1) {
      for (const type of ['dragEnter', 'dragOver', 'dragOver', 'dragOver', 'drop']) {
        await contents.debugger.sendCommand('Input.dispatchDragEvent', { type, x: 240 + attempt, y: 240, data: { items: [], files: [file], dragOperationsMask: 1 } });
        await wait(type === 'drop' ? 0 : 60);
      }
      result.dropped = await until(() => contents.executeJavaScript('window.__dropped', true), 1500);
      result.attempts = attempt + 1;
    }
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
  } finally {
    try {
      contents.debugger.detach();
    } catch {
      /* it was not attached */
    }
    await contents.executeJavaScript('window.__stopDrop && window.__stopDrop(); true', true).catch(() => undefined);
  }
  return result;
}

/**
 * Load, exercise `window.flash` from inside the page, write the report and quit. The
 * page opens the document itself (it is told the path by `startup()`), exactly as in normal
 * use; the report then says what the harness holds. A built interface also reports what it
 * saw (`reportVerification`) and the run fails when it says it did not succeed.
 */
async function smoke(window, reportPath) {
  const report = { started: new Date().toISOString(), electron: process.versions.electron, node: process.versions.node, checks: {} };
  const finish = (code) => {
    report.finished = new Date().toISOString();
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
    app.exit(code);
  };
  window.webContents.once('did-fail-load', (_e, code, description) => {
    report.error = `did-fail-load ${code} ${description}`;
    finish(1);
  });
  window.webContents.once('did-finish-load', async () => {
    try {
      const surface = `(async () => {
        const out = { api: Object.keys(window.flash).sort(), frozen: Object.isFrozen(window.flash) };
        out.isolated = { process: typeof process, require: typeof require, module: typeof module };
        out.startup = await window.flash.startup();
        const engines = await window.flash.engines();
        out.engines = engines.map((e) => ({ id: e.id, kind: e.kind, available: e.available, levels: e.levels_line }));
        const caps = await window.flash.call('capabilities', {});
        out.capabilities = { ok: caps.ok, served_by: caps.served_by, error: caps.error };
        out.refused = (await window.flash.call('format_disk', {})).error;
        out.badArgs = (await window.flash.call('header', { x: () => 1 }).catch((e) => ({ threw: String(e) })));
        out.badEvidence = (await window.flash.reportVerification({ nested: () => 1 }).catch((e) => ({ threw: String(e) })));
        out.asset = window.flash.assetUrl('C:/x y/z.png');
        return out;
      })()`;
      report.checks = await window.webContents.executeJavaScript(surface, true);
      const swf = cli.open;
      if (swf) report.checks.drop = await simulateDrop(window.webContents, swf);

      // The page opens the document; what the harness holds is then the same for the console and for the interface.
      if (swf) {
        const session = await until(() => harness.session && harness.session.revision !== null && harness.session, 30000);
        if (session) {
          const header = await harness.request('header', {});
          const tree = await harness.request('tag_tree', {});
          const units = await harness.request('scripts', { limit: 1 });
          const vote = units.ok && units.result.items.length > 0 ? await harness.request('decompile', { unit_id: units.result.items[0].id }) : null;
          report.checks.session = {
            id: session.id,
            revision: session.revision,
            opened_by: session.openedBy,
            header: header.ok ? { served_by: header.served_by, signature: header.result.signature, version: header.result.version, size: header.result.frame_size, tag_count: header.result.tag_count } : header.error,
            tag_tree: tree.ok ? { served_by: tree.served_by, folders: tree.result.nodes.map((n) => n.folder || n.id) } : tree.error,
            vote: vote && vote.ok && vote.result.selection ? { unit: units.result.items[0].id, chosen: vote.result.selection.engine, candidates: vote.result.selection.candidates.map((c) => [c.engine, c.ok, c.score, c.error]) } : vote && vote.error,
          };
        } else {
          report.checks.session = null;
        }
      }

      if (realInterface()) {
        // A verification run waits for the interface's own verdict (`npm run smoke`).
        const evidence = swf ? await waitForVerification(Number(process.env.FLASH_SMOKE_UI_TIMEOUT_MS) || 90000) : null;
        report.checks.ui = evidence;
        report.ok = Boolean(!swf || (evidence && evidence.success === true));
      } else {
        // The console fills its engine table and, with a document, says it opened it.
        const rows = `(async () => { for (let i = 0; i < 40; i += 1) { const n = document.querySelectorAll('#engines tbody tr').length; if (n > 0) return n; await new Promise((r) => setTimeout(r, 100)); } return 0; })()`;
        report.checks.pageRows = await window.webContents.executeJavaScript(rows, true);
        if (swf) {
          report.checks.pageDocument = await until(
            () => window.webContents.executeJavaScript(`(document.getElementById('document') || {}).textContent || ''`, true).then((text) => (/sesión/.test(text) ? text : '')),
            20000,
          );
        }
        report.ok = report.checks.pageRows > 0 && (!swf || Boolean(report.checks.pageDocument));
      }
      // The file dropped on the window must have reached the page as its path.
      if (swf && !(report.checks.drop && report.checks.drop.dropped === swf)) report.ok = false;
      if (cli.smokeShot) {
        await new Promise((resolve) => setTimeout(resolve, 800));
        fs.writeFileSync(cli.smokeShot, (await window.webContents.capturePage()).toPNG());
      }
      finish(report.ok ? 0 : 1);
    } catch (error) {
      report.error = String(error && error.stack ? error.stack : error);
      finish(1);
    }
  });
}

async function shutdown() {
  if (quitting) return;
  quitting = true;
  try {
    if (harness) await harness.close();
  } finally {
    app.exit(0);
  }
}

const gotLock = process.env.FLASH_ALLOW_MULTIPLE === '1' || cli.smokeReport ? true : app.requestSingleInstanceLock();
if (!gotLock) {
  app.exit(0);
} else {
  app.on('second-instance', (_event, argv) => {
    const next = parseArgs(argv);
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
      if (next.open) mainWindow.webContents.send('flash:menu', { id: 'file:open-path', path: next.open });
    }
  });

  app.whenReady().then(() => {
    harness = createHarness({
      packaged: app.isPackaged,
      resourcesPath: resourcesRoot,
      env: process.env,
      electron: true,
      nodePath: process.execPath,
      supervise: process.env.FLASH_NO_SUPERVISOR !== '1',
    });
    for (const event of ['engine-start', 'engine-exit']) {
      harness.on(event, (payload) => {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('flash:engine-event', { event, ...payload });
      });
    }
    registerProtocols();
    hardenSession();
    registerIpc();
    Menu.setApplicationMenu(
      Menu.buildFromTemplate(
        buildMenuTemplate({
          devTools: !app.isPackaged || process.env.FLASH_DEVTOOLS === '1',
          send: (id) => {
            if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('flash:menu', { id });
          },
        }),
      ),
    );
    const window = createWindow();
    if (cli.smokeReport) smoke(window, cli.smokeReport);
  });

  app.on('window-all-closed', () => {
    shutdown();
  });
  app.on('before-quit', (event) => {
    if (!quitting) {
      event.preventDefault();
      shutdown();
    }
  });
  app.on('render-process-gone', (_event, _contents, details) => log('el renderizador terminó:', details.reason));
  process.on('exit', () => {
    // Last resort when the process dies without a clean shutdown: no worker outlives us.
    try {
      if (harness) harness.cancel();
    } catch {
      /* nothing left to do */
    }
  });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, shutdown);
}
