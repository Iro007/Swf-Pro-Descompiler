'use strict';
/**
 * The harness console. It is the window the application shows until the React UI is
 * built (`ui/dist`) and doubles as a manual check of every engine. It only uses
 * `window.flash`, exactly as the real UI will.
 */
const $ = (id) => document.getElementById(id);

function cell(row, text, className) {
  const td = document.createElement('td');
  td.textContent = text;
  if (className) td.className = className;
  row.appendChild(td);
}

function setStatus(text, bad = false) {
  const status = $('status');
  status.textContent = text;
  status.className = bad ? 'bad' : 'muted';
}

async function showEngines() {
  const engines = await window.flash.engines();
  const body = $('engines').querySelector('tbody');
  body.replaceChildren();
  for (const engine of engines.filter((e) => e.kind !== 'tool')) {
    const row = document.createElement('tr');
    cell(row, engine.name + (engine.via ? ` (vía ${engine.via})` : ''));
    cell(row, engine.available ? 'disponible' : 'no disponible', engine.available ? 'ok' : 'bad');
    cell(row, engine.levels_line, 'muted');
    const runtime = engine.runtime;
    const detail = engine.available
      ? runtime && runtime.running
        ? `en ejecución · pid ${runtime.pid}${runtime.supervised ? ' · supervisado' : ''}`
        : 'se inicia al usarlo'
      : engine.reason;
    cell(row, detail, 'muted');
    body.appendChild(row);
  }
}

function describeFailure(reply) {
  const attempts = (reply.error.attempts || []).map((a) => `${a.engine}: ${a.ok ? 'ok' : a.error}`).join(' | ');
  return `${reply.error.message}${attempts ? `\n${attempts}` : ''}`;
}

function summarize(opened, header, tree, scripts) {
  const lines = [];
  lines.push(`${opened.result.info.filename} · sesión ${opened.session_id.slice(0, 8)} · revisión ${opened.revision}`);
  lines.push(`abierto por ${opened.served_by} · sha256 ${opened.sha256.slice(0, 16)}…`);
  if (header.ok) {
    const h = header.result;
    const size = h.frame_size ? `${h.frame_size.width}×${h.frame_size.height}` : 'n/d';
    lines.push(`${h.signature} v${h.version} · ${size} · ${h.frame_rate} fps · ${h.frame_count} ${h.frame_count === 1 ? 'cuadro' : 'cuadros'} · ${h.tag_count ?? '?'} tags (cabecera: ${header.served_by})`);
  }
  if (tree.ok) {
    lines.push(`árbol (${tree.served_by}): ${tree.result.nodes.map(folderText).join(' · ')}`);
  }
  if (scripts.ok) lines.push(`${scripts.result.total} unidades de código (${scripts.served_by})`);
  return lines.join('\n');
}

/** `Cuadros 3`, `Scripts 12`: a folder of the tree with its count. PySWF and RustSWF name the count differently. */
function folderText(node) {
  const name = node.kind === 'header' ? 'Cabecera' : node.label || node.folder || node.id;
  const count = node.child_count ?? node.count;
  return count ? `${name} ${count}` : name;
}

/**
 * The code units a tree offers: a class lists its members (PySWF) or is itself a unit (RustSWF),
 * a script is a unit.
 */
function collect(nodes, out = []) {
  for (const node of nodes) {
    if (node.kind === 'class' && Array.isArray(node.members)) {
      const owner = node.detail ? `${node.detail}.${node.label}` : node.label;
      for (const member of node.members) out.push({ unit_id: member.unit_id, label: `${owner}.${member.name}` });
    } else if ((node.kind === 'class' || node.kind === 'script') && node.unit_id) {
      out.push({ unit_id: node.unit_id, label: node.qname || node.label });
    }
    if (node.children) collect(node.children, out);
  }
  return out;
}

async function openDocument(path) {
  $('document').textContent = 'Abriendo…';
  $('units').replaceChildren();
  $('decompile').disabled = true;
  const opened = await window.flash.call('open', { path });
  if (!opened.ok) {
    $('document').textContent = describeFailure(opened);
    setStatus('No se pudo abrir el archivo', true);
    return;
  }
  const [header, tree, scripts] = await Promise.all([
    window.flash.call('header', {}),
    window.flash.call('tag_tree', {}),
    window.flash.call('scripts', { limit: 1 }),
  ]);
  $('document').textContent = summarize(opened, header, tree, scripts);
  $('served').textContent = `servido por ${opened.served_by}`;
  const units = tree.ok ? collect(tree.result.nodes.filter((n) => n.folder === 'scripts')) : [];
  for (const unit of units) {
    const option = document.createElement('option');
    option.value = unit.unit_id;
    option.textContent = `${unit.label}  (${unit.unit_id})`;
    $('units').appendChild(option);
  }
  $('decompile').disabled = units.length === 0;
  setStatus(`${units.length} unidades de código en el árbol`);
  await showEngines();
}

async function decompile() {
  const unit = $('units').value;
  $('source').textContent = 'Descompilando con todos los motores…';
  const reply = await window.flash.call('decompile', { unit_id: unit });
  const votes = $('votes');
  const body = votes.querySelector('tbody');
  body.replaceChildren();
  if (!reply.ok) {
    votes.hidden = true;
    $('source').textContent = describeFailure(reply);
    return;
  }
  const { selection } = reply.result;
  for (const candidate of selection.candidates) {
    const row = document.createElement('tr');
    cell(row, candidate.name + (candidate.engine === selection.engine ? '  ◀ elegido' : ''), candidate.engine === selection.engine ? 'ok' : '');
    cell(row, candidate.score === null ? '—' : String(candidate.score));
    cell(row, candidate.ok ? candidate.state : candidate.error, candidate.ok ? '' : 'bad');
    cell(row, String(candidate.ms), 'muted');
    body.appendChild(row);
  }
  votes.hidden = false;
  $('source').textContent = reply.result.source ?? `(sin fuente; P-code de ${reply.served_by})\n\n${reply.result.pcode}`;
  await showEngines();
}

async function start() {
  const startup = await window.flash.startup();
  setStatus(`Electron ${startup.electron} · Node ${startup.node} · interfaz: ${startup.ui}`);
  await showEngines();
  $('open').addEventListener('click', async () => {
    const path = await window.flash.openDialog('swf');
    if (path) await openDocument(path);
  });
  $('cancel').addEventListener('click', () => window.flash.cancel());
  $('decompile').addEventListener('click', decompile);
  window.flash.onMenu(async ({ id, path }) => {
    if (id === 'file:open') $('open').click();
    else if (id === 'file:open-path' && path) await openDocument(path);
    else if (id === 'tools:cancel') await window.flash.cancel();
    else if (id === 'tools:engines') await showEngines();
  });
  window.flash.onEngineEvent(() => showEngines());
  if (startup.path) await openDocument(startup.path);
}

start().catch((error) => setStatus(String(error), true));
