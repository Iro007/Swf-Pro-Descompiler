'use strict';
/**
 * The native menu: Archivo, Herramientas, Ayuda. Every command item sends its `id` to the
 * renderer (`window.flash.onMenu(({id}) => ...)`); the application decides what it means,
 * the menu has no logic. Pure template builder, so it is testable without Electron.
 */

/** Menu event ids, for the renderer. */
const MENU_EVENTS = Object.freeze([
  'file:open', 'file:open-project', 'file:save-project', 'file:save-swf', 'file:export-all', 'file:close',
  'tools:engines', 'tools:validate', 'tools:undo', 'tools:redo', 'tools:cancel', 'tools:preview', 'tools:benchmarks',
  'help:protocol', 'help:about',
]);

/**
 * @param {{send: (id: string) => void, devTools?: boolean}} options
 * @returns {Array<object>} an Electron menu template
 */
function buildMenuTemplate({ send, devTools = false }) {
  const item = (id, label, extra = {}) => ({ id, label, click: () => send(id), ...extra });
  return [
    {
      label: 'Archivo',
      submenu: [
        item('file:open', 'Abrir SWF…', { accelerator: 'CmdOrCtrl+O' }),
        item('file:open-project', 'Abrir proyecto…', { accelerator: 'CmdOrCtrl+Shift+O' }),
        { type: 'separator' },
        item('file:save-project', 'Guardar proyecto…', { accelerator: 'CmdOrCtrl+S' }),
        item('file:save-swf', 'Guardar SWF como…', { accelerator: 'CmdOrCtrl+Shift+S' }),
        item('file:export-all', 'Exportar todos los recursos…'),
        { type: 'separator' },
        item('file:close', 'Cerrar documento', { accelerator: 'CmdOrCtrl+W' }),
        { type: 'separator' },
        { role: 'quit', label: 'Salir' },
      ],
    },
    {
      label: 'Herramientas',
      submenu: [
        item('tools:engines', 'Motores y herramientas'),
        item('tools:validate', 'Validar SWF'),
        item('tools:preview', 'Vista previa en Ruffle'),
        item('tools:benchmarks', 'Benchmarks'),
        { type: 'separator' },
        // Shown with their shortcut but not registered: a text field's own undo must keep working.
        item('tools:undo', 'Deshacer cambio en el documento', { accelerator: 'CmdOrCtrl+Alt+Z', registerAccelerator: false }),
        item('tools:redo', 'Rehacer cambio en el documento', { accelerator: 'CmdOrCtrl+Alt+Y', registerAccelerator: false }),
        { type: 'separator' },
        item('tools:cancel', 'Cancelar la operación en curso'),
      ],
    },
    {
      label: 'Ayuda',
      submenu: [
        item('help:protocol', 'Motores y niveles (L1 a L5)'),
        ...(devTools ? [{ type: 'separator' }, { role: 'toggleDevTools', label: 'Herramientas de desarrollo' }, { role: 'reload', label: 'Recargar' }] : []),
        { type: 'separator' },
        item('help:about', 'Acerca de Flash Universal Studio'),
      ],
    },
  ];
}

/** Every `id` a template can send. */
function menuIds(template) {
  const ids = [];
  const visit = (items) => {
    for (const entry of items) {
      if (entry.id) ids.push(entry.id);
      if (entry.submenu) visit(entry.submenu);
    }
  };
  visit(template);
  return ids;
}

module.exports = { MENU_EVENTS, buildMenuTemplate, menuIds };
