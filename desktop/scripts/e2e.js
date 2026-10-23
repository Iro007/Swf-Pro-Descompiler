#!/usr/bin/env node
'use strict';
/**
 * End to end through the harness, with the real engines and no window: open a SWF,
 * print its header, the tag tree, and decompile one AS3 class in `auto` mode showing
 * which engine the vote chose and why.
 *
 *   node scripts/e2e.js [file.swf] [--unit tag:6/class:1] [--supervise]
 *
 * Engines are found like the application finds them (this checkout, FLASH_ENGINE_ROOTS and,
 * in a git worktree, the main checkout that holds the build outputs).
 */
const path = require('node:path');
const { realHarness, PARITY, realRoots } = require('../testkit/real');

const argv = process.argv.slice(2);
const flag = (name) => {
  const at = argv.indexOf(name);
  return at < 0 ? null : argv[at + 1];
};
const file = argv.find((a) => /\.(swf|pyswfproj)$/i.test(a)) || PARITY('dev-as3-classes-namespaces');
const harness = realHarness({ supervise: argv.includes('--supervise') });

const line = (text = '') => console.log(text);
const pad = (value, width) => String(value).padEnd(width);

function printEngines() {
  line('Motores');
  for (const e of harness.listEngines().filter((x) => x.kind !== 'tool')) {
    line(`  ${pad(e.id, 7)} ${pad(e.available ? 'disponible' : 'NO disponible', 14)} ${e.levels_line}${e.available ? '' : `   (${e.reason})`}`);
  }
}

/** Classes of the tree: PySWF names the document of a class `document_id`, RustSWF `unit_id`. */
function collect(nodes, out = []) {
  for (const node of nodes) {
    if (node.kind === 'class' && (node.document_id || node.unit_id)) out.push({ ...node, unit_id: node.document_id || node.unit_id });
    if (node.children) collect(node.children, out);
  }
  return out;
}

function printVote(reply) {
  const { selection } = reply.result;
  line(`  elegido: ${selection.name} (${selection.engine}) con ${selection.score ? selection.score.value : 'sin puntuación'}`);
  for (const c of selection.candidates) {
    const mark = c.engine === selection.engine ? '*' : ' ';
    line(`  ${mark} ${pad(c.engine, 7)} puntuación ${pad(c.score === null ? '—' : c.score, 4)} ${pad(c.ok ? c.state : `FALLÓ ${c.kind}: ${c.error}`, 40)} ${c.ms} ms`);
  }
}

async function main() {
  const started = Date.now();
  line(`Archivo: ${file}`);
  line(`Raíces de motores: ${realRoots().join(' ; ')}`);
  printEngines();

  const opened = await harness.call('open', { path: file });
  line();
  line(`open        servido por ${opened.served_by}, revisión ${opened.revision}, sha256 ${opened.sha256.slice(0, 16)}…, ${opened.elapsed_ms} ms`);

  const header = await harness.call('header', {});
  const h = header.result;
  line(`header      servido por ${header.served_by}: ${h.signature} v${h.version}, ${h.frame_size ? `${h.frame_size.width}x${h.frame_size.height}` : '?'} px, ${h.frame_rate} fps, ${h.frame_count} cuadros, ${h.tag_count} tags`);
  if (h.file_attributes) line(`            as3=${h.file_attributes.actionscript3} unidades=${h.script_count} fondo=${h.background_color} límites=${JSON.stringify(h.script_limits)}`);

  const tree = await harness.call('tag_tree', {});
  line(`tag_tree    servido por ${tree.served_by}: ${tree.result.node_count ?? tree.result.total_tags} nodos, revisión ${String(tree.result.revision).slice(0, 12)}…`);
  for (const folder of tree.result.nodes) {
    line(`  ${pad(folder.kind === 'header' ? 'header' : folder.folder, 12)} ${pad(folder.label, 14)} ${(folder.child_count ?? folder.count) === undefined ? '' : folder.child_count ?? folder.count}`);
    if (folder.folder === 'scripts') {
      const walk = (nodes, depth) => {
        for (const node of nodes.slice(0, 8)) {
          const unit = node.document_id || node.unit_id;
          line(`    ${'  '.repeat(depth)}${node.kind} ${node.label}${unit ? `  [${unit}]` : ''}${node.members ? `  (${node.members.length} miembros)` : ''}`);
          if (node.children && depth < 2) walk(node.children, depth + 1);
        }
      };
      walk(folder.children, 0);
    }
  }

  const classes = collect(tree.result.nodes);
  const unit = flag('--unit') || (classes[classes.length - 1] || {}).unit_id;
  if (unit) {
    line();
    line(`decompile (auto) de la clase ${unit}`);
    const klass = await harness.call('decompile', { unit_id: unit });
    printVote(klass);
    line(`  servido por ${klass.served_by}${klass.via ? ` (vía ${klass.via})` : ''}, ${klass.elapsed_ms} ms; fuente:`);
    const source = klass.result.source || `(sin fuente: P-code de ${klass.served_by})\n${klass.result.pcode}`;
    for (const text of source.split('\n').slice(0, 14)) line(`    | ${text}`);
  }

  const scripts = await harness.call('scripts', { limit: 50 });
  const method = scripts.result.items.find((u) => u.language === 'as3' && /method:5$/.test(u.id)) || scripts.result.items[0];
  if (method) {
    line();
    line(`decompile (auto) del método ${method.id}`);
    const reply = await harness.call('decompile', { unit_id: method.id });
    printVote(reply);
    line(`  servido por ${reply.served_by}`);
  }

  line();
  const status = harness.listEngines().filter((e) => e.runtime && e.runtime.running);
  line(`Procesos en marcha: ${status.map((e) => `${e.id}(pid ${e.runtime.pid}${e.runtime.supervised ? ', supervisado' : ''})`).join(', ') || 'ninguno'}`);
  line(`Total: ${Date.now() - started} ms`);
}

main()
  .catch((error) => {
    console.error('FALLO', error && error.stack ? error.stack : error);
    if (error && error.attempts) console.error(JSON.stringify(error.attempts, null, 2));
    process.exitCode = 1;
  })
  .finally(() => harness.close());
