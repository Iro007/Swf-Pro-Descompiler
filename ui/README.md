# Interfaz de Flash Universal Studio

Una sola ventana con el aspecto de JPEXS (`src/shell/`): menú, barra de herramientas, árbol del SWF y panel de contenido. `App.tsx` solo monta `MainWindow`.

## Estructura

- `shell/MainWindow.tsx` reúne menú, árbol, panel y diálogos alrededor de un único documento. `useSession.ts` guarda ese documento (estado de la suite, árbol, catálogo de motores, borradores) y serializa las operaciones que lo modifican.
- `shell/TagTree.tsx` (árbol virtualizado con filtro y teclado), `DisplayPanel.tsx` (elige la tarjeta según el nodo), `ScriptPanel.tsx` (fuente | P-code con CodeMirror 6), `PreviewPanel.tsx`, `HexPanel.tsx`, `HeaderPanel.tsx`, `TagInfoPanel.tsx` + `ResourceEditor.tsx`, `SourceFilePanel.tsx`, `StatusBar.tsx` y `dialogs/` (compilar, exportar, motores, benchmarks, ensamblado ABC).
- `api.ts` es el único módulo que habla con el anfitrión de escritorio, Electron (`window.flash` del preload: operaciones de la suite, selectores de archivo, arranque, arrastrar y soltar). El resto de la interfaz no importa nada del anfitrión: cambiar de anfitrión es reescribir ese archivo.
- `displayNames.ts` cambia únicamente los nombres de presentación: los identificadores `rust`, `python` y `native` permanecen en el protocolo, las campañas y los proyectos existentes.

## Tema y almacenamiento

Los colores salen de variables en `index.css`; `data-theme` (`light`/`dark`) la fija el menú *Ver* o el sistema. Las preferencias (tema, ancho del árbol, reparto fuente/P-code, motor preferido, archivos recientes) se guardan en `localStorage` protegido con `try/catch`: la ventana funciona igual sin almacenamiento.

## Operaciones de la suite que usa

`tag_tree`, `header`, `raw`, `resource_preview`, `resource_info`, `resource_edit`, `decompile`, `compare`, `edit`, `source_files`, `source_edit`, `undo`, `redo`, `open`, `load_project`, `save_project`, `export_swf`, `export`, `compile_project`, `compile_saved`, `tool_operation`, `timeline`, `preview_data`, `engines`, `configure_engine`, `launch_tool`, `bench_results`, `benchmark_tools` y `validate`. Todas pasan por `api.suiteRpc`.

## Comprobaciones

- `npm run lint` (TypeScript) y `npm run build`.
- `tests/verify-suite-ui.py`: contrato de la ventana con un anfitrión simulado (Playwright; sirve la compilación con `npm run preview -- --port 4174`).
- `tests/verify-suite-native-ui.py`: aplicación Electron con el worker real a través del puerto de depuración (`--remote-debugging-port`; requiere la aplicación empaquetada o `npm start --prefix desktop`; no se ejecuta en esta campaña).

La edición de fuentes aportadas y la reconstrucción desde SWF son capacidades distintas: un proyecto Haxe conserva sus `.hx` originales y el visor no promete recuperarlos desde bytecode. Los tipos de recursos sin edición compatible mantienen sus bytes y diagnósticos. La vista de contornos limita el dibujo a 3000 puntos; el índice permite editar cualquiera de los puntos que entrega el backend.

Pendientes: transformaciones y arrastre profesionales, edición completa de curvas y estilos, edición de P-code desde la ventana (el panel es de solo lectura; `edit` con `mode: "pcode"` sigue disponible por RPC), y verificación de FLA/XFL con Animate. Los módulos comerciales requieren instalación legítima y una campaña observada; su presencia en el gestor no acredita paridad.
