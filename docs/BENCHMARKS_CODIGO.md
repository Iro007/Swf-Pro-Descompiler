# Benchmarks

Una pregunta por categoría; hoy solo **descompiladores**: lo que emite el motor, ¿se recompila con un compilador independiente (Flex para AS3, Rascal para AS2) y Ruffle imprime la misma traza que el original?

```powershell
.\medir-codigo.ps1            # rápido: un caso por familia de desarrollo (~4 min)
.\medir-codigo.ps1 -Completo  # todos los casos de desarrollo y validación
.\medir-codigo.ps1 -Motores native rust
```

- Corpus fijo: `benchmark_inputs/parity-20261014` (sellado con hashes; con fuente original y traza esperada).
- Salida: `reports/bench/<ejecución>/results.json` (unos KB) e `index.json`. La pestaña Benchmarks de la app lo lee.
- Cada celda sin número lleva su motivo (no instalado, no lee ese lenguaje, fuente parcial…). Con una ventaja de un caso o menos no se declara ganador.
- Pendiente: tablas de compiladores, desensambladores (ida y vuelta) y extractores.
