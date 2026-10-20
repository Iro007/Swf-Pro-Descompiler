import { useState } from "react";
import { Download, Package } from "lucide-react";
import {
  batchExportImages,
  batchExportScripts,
  batchExportShapes,
} from "../api";
import { TagInfo } from "../types";
import { errorMessage, GROUPS, saveBlob } from "../utils/workspace";

interface Props {
  sid: string;
  filename: string;
  tags: TagInfo[];
  onError: (message: string) => void;
  onExportAll: () => void;
}

export default function ExportPanel({
  sid,
  filename,
  tags,
  onError,
  onExportAll,
}: Props) {
  const [type, setType] = useState("images");
  const [format, setFormat] = useState("png");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const indices = tags
    .filter((tag) =>
      GROUPS.find((group) => group.id === type)?.codes.includes(tag.code),
    )
    .map((tag) => tag.index);
  async function exportSelected() {
    setBusy(true);
    setResult("");
    try {
      const request = { tag_indices: indices, format };
      const response =
        type === "images"
          ? await batchExportImages(sid, request)
          : type === "shapes"
            ? await batchExportShapes(sid, request)
            : await batchExportScripts(sid, request);
      let count = 0;
      for (const item of response.results) {
        const code = (item as typeof item & { code?: string }).code;
        if (item.error || (!item.data && !code)) continue;
        const content =
          type === "scripts" ? code || item.data || "" : item.data || "";
        const blob =
          type === "images"
            ? new Blob([
                Uint8Array.from(atob(content), (char) => char.charCodeAt(0)),
              ])
            : new Blob([content], {
                type: type === "shapes" ? "image/svg+xml" : "text/plain",
              });
        saveBlob(blob, `${filename}_tag${item.index}.${item.format || format}`);
        count += 1;
      }
      setResult(
        `${count}/${indices.length} recursos exportados${response.results.some((item) => item.error) ? ". Algunos recursos tienen errores de decodificación." : "."}`,
      );
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="export-panel">
      <p className="muted">
        Descarga toda la biblioteca en un ZIP o elige un tipo de recurso. El
        archivo de trabajo se conserva.
      </p>
      <button
        className="studio-button primary full-width"
        disabled={busy}
        onClick={onExportAll}
      >
        <Package size={16} />
        Exportar biblioteca completa · ZIP
      </button>
      <div className="section-caption">POR TIPO DE RECURSO</div>
      <label>
        Tipo
        <select
          value={type}
          aria-label="Tipo de exportación"
          onChange={(event) => {
            const next = event.target.value;
            setType(next);
            setFormat(
              next === "images" ? "png" : next === "shapes" ? "svg" : "asm",
            );
            setResult("");
          }}
        >
          <option value="images">Imágenes</option>
          <option value="shapes">Formas y vectores</option>
          <option value="scripts">Scripts / P-code</option>
        </select>
      </label>
      <label>
        Formato
        <select
          value={format}
          aria-label="Formato de exportación"
          onChange={(event) => setFormat(event.target.value)}
        >
          {type === "images" ? (
            <>
              <option value="png">PNG</option>
              <option value="jpg">JPEG</option>
            </>
          ) : type === "shapes" ? (
            <option value="svg">SVG vectorial</option>
          ) : (
            <option value="asm">ASM / P-code</option>
          )}
        </select>
      </label>
      <p className="muted">
        {indices.length} recursos disponibles. La exportación por tipo descarga
        un archivo por recurso.
      </p>
      <button
        className="studio-button secondary full-width"
        disabled={busy || !indices.length}
        onClick={() => void exportSelected()}
      >
        <Download size={15} />
        {busy ? "Exportando…" : "Exportar este tipo"}
      </button>
      {result && (
        <p className="export-result" role="status">
          {result}
        </p>
      )}
    </div>
  );
}
