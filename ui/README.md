# Interfaz de Flash Universal Studio

Entrega siguiente 0.3.0. Los controles nuevos se verifican en esta campaña con TypeScript y build, sin abrir GUI, navegador ni CDP.

Los motores propios se muestran como **RustSWF** y **PySWF**. `displayNames.ts` cambia únicamente los nombres de presentación: los identificadores `rust`, `python` y `native` permanecen en el protocolo, las campañas y los proyectos existentes.

En **Proyectos e historial**, selecciona una fuente conservada para editar ActionScript, Haxe, HXML o Project.xml. **Guardar fuente** conserva el texto del proyecto; **Recompilar proyecto** utiliza las fuentes guardadas y escribe un nuevo SWF. La interfaz indica los cambios pendientes y conserva los borradores al cambiar de panel o cuando el backend rechaza una operación. Cada guardado comprueba el hash del SWF y el hash de la fuente.

En **Recursos**, el tipo devuelto por `resource_info` habilita la operación correspondiente: sustituir imagen o audio, editar texto, mover un vértice de una forma o glifo, y cambiar nombre, estilo y métricas de fuentes compatibles. Las formas usan píxeles con precisión de 0,05 px; los glifos conservan sus unidades EM originales. La vista de contornos facilita seleccionar puntos y revisar las coordenadas, sin ejecutar ActionScript.

La edición de fuentes aportadas y la reconstrucción desde SWF son capacidades distintas. Un proyecto Haxe conserva sus `.hx` originales; el visor no promete recuperarlos desde bytecode. Los tipos de recursos sin edición compatible mantienen sus bytes y diagnósticos.

Verificación ligera completada desde esta carpeta: `npm run lint` y `npm run build`. Estas comprobaciones validan TypeScript y el bundle; no equivalen a una prueba gráfica ni de comportamiento de todas las películas. Las capturas de 0.2.0 son históricas. La vista de contornos limita el dibujo a 3000 puntos para acotar recursos; el índice sigue permitiendo editar cualquier punto que entregue el backend.

Permanecen pendientes transformaciones/arrastre profesionales, edición completa de controles de curvas y estilos, variantes de recursos no soportadas y verificación de FLA/XFL con Animate. Los módulos comerciales requieren instalación legítima y una campaña observada; su presencia en el gestor no acredita paridad.
