# py_swf_editor — Web UI

React + Vite frontend for the Python SWF decompiler/editor backend (`server/`, FastAPI).

## Desarrollo

Arranca el backend y el dev-server de Vite (con proxy `/api` → `127.0.0.1:8000`):

```bash
# terminal 1 (raíz del repo)
python -m uvicorn server.app:app --port 8000

# terminal 2
cd web
npm install
npm run dev
```

## Producción

```bash
cd web && npm run build
python -m server   # sirve web/dist y abre el navegador
```

Todo el parseo de SWF ocurre en el backend Python (`py_swf/`); este frontend solo consume
la API (`web/src/api.ts`).
