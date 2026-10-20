import { useState } from "react";
import {
  Camera,
  Check,
  GitCompareArrows,
  History,
  Loader2,
  RotateCcw,
} from "lucide-react";
import {
  compareSnapshots,
  createSnapshot,
  ProjectHistory,
  restoreSnapshot,
  SnapshotComparison,
} from "../api";
import { errorMessage, formatBytes } from "../utils/workspace";

interface Props {
  sid: string;
  history: ProjectHistory;
  onRefresh: () => Promise<void>;
  onRestored: () => Promise<void>;
  onSelectTag: (index: number) => void;
  onError: (message: string) => void;
}

const operations: Record<string, string> = {
  edit: "Edición de recurso",
  assemble: "P-code ensamblado",
  compile_source: "Fuente recompilada",
  replace_text: "Texto reemplazado",
  replace_resource: "Recurso reemplazado",
  undo: "Cambio deshecho",
  redo: "Cambio rehecho",
  restore_snapshot: "Snapshot restaurado",
};

export default function HistoryPanel({
  sid,
  history,
  onRefresh,
  onRestored,
  onSelectTag,
  onError,
}: Props) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [comparison, setComparison] = useState<SnapshotComparison | null>(null);
  const [comparedName, setComparedName] = useState("");

  async function run(task: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    try {
      await task();
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="history-panel">
      <div className="inspector-title">
        <History size={17} />
        <h3>Historial de trabajo</h3>
      </div>
      <p className="muted">
        Guarda un punto de retorno antes de editar. El SWF original se conserva
        en el proyecto.
      </p>
      <form
        className="snapshot-form"
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            await createSnapshot(sid, name.trim());
            setName("");
            setComparison(null);
            await onRefresh();
          });
        }}
      >
        <label htmlFor="snapshot-name">Nuevo snapshot</label>
        <input
          id="snapshot-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Ej. Antes de cambiar imágenes"
          maxLength={120}
        />
        {history.snapshots.includes(name.trim()) && (
          <p className="warning">
            Ya existe. Usa otro nombre para conservarlo.
          </p>
        )}
        <button
          className="studio-button secondary"
          disabled={
            busy || !name.trim() || history.snapshots.includes(name.trim())
          }
        >
          <Camera size={15} />
          {busy ? "Procesando…" : "Crear snapshot"}
        </button>
      </form>
      <div className="section-caption">
        SNAPSHOTS <span>{history.snapshots.length}</span>
      </div>
      {!history.snapshots.length && (
        <p className="muted">Aún no hay snapshots.</p>
      )}
      {history.snapshots.map((snapshot) => (
        <div className="snapshot-card" key={snapshot}>
          <strong>{snapshot}</strong>
          <div className="snapshot-actions">
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  setComparison(await compareSnapshots(sid, snapshot));
                  setComparedName(snapshot);
                })
              }
            >
              <GitCompareArrows size={13} />
              Comparar
            </button>
            <button disabled={busy} onClick={() => setRestoring(snapshot)}>
              <RotateCcw size={13} />
              Restaurar
            </button>
          </div>
          {restoring === snapshot && (
            <div className="restore-confirm">
              <p>¿Restaurar «{snapshot}»? Podrás deshacer este cambio.</p>
              <button
                disabled={busy}
                className="studio-button secondary"
                onClick={() =>
                  void run(async () => {
                    await restoreSnapshot(sid, snapshot);
                    setRestoring(null);
                    setComparison(null);
                    await onRestored();
                  })
                }
              >
                Confirmar restauración
              </button>
              <button
                className="text-button"
                onClick={() => setRestoring(null)}
              >
                Cancelar
              </button>
            </div>
          )}
        </div>
      ))}
      {busy && (
        <p className="loading-line" role="status">
          <Loader2 size={13} className="animate-spin" />
          Procesando proyecto…
        </p>
      )}
      {comparison && (
        <section className="snapshot-diff" aria-label="Comparación de snapshot">
          <strong>{comparedName} → trabajo actual</strong>
          {comparison.identical ? (
            <p className="success">
              <Check size={14} />
              Sin diferencias
            </p>
          ) : (
            <>
              <p className="muted">
                {comparison.changed_tags.length} tags modificados
                {comparison.header_changed ? " · cabecera distinta" : ""}
              </p>
              {comparison.changed_tags.slice(0, 50).map((tag) => (
                <button
                  key={tag.index}
                  onClick={() => onSelectTag(tag.index)}
                  className="diff-tag"
                >
                  <span>
                    #{tag.index} {tag.name}
                  </span>
                  <small>
                    {formatBytes(tag.before_size)} →{" "}
                    {formatBytes(tag.after_size)}
                  </small>
                </button>
              ))}
              {comparison.changed_tags.length > 50 && (
                <small>Se muestran las primeras 50 diferencias.</small>
              )}
            </>
          )}
        </section>
      )}
      <div className="section-caption">
        OPERACIONES <span>{history.entries.length}</span>
      </div>
      {!history.entries.length && (
        <p className="muted">
          Los cambios aparecerán aquí cuando edites un recurso.
        </p>
      )}
      <ol className="operation-list">
        {history.entries
          .slice()
          .reverse()
          .map((entry, index) => (
            <li key={`${entry.at}-${index}`}>
              <span className="operation-dot" />
              <div>
                <strong>
                  {operations[entry.operation] || entry.operation}
                </strong>
                <small>
                  {entry.at
                    ? new Date(entry.at).toLocaleTimeString("es", {
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : ""}
                  {entry.metadata?.name ? ` · ${entry.metadata.name}` : ""}
                </small>
              </div>
            </li>
          ))}
      </ol>
    </div>
  );
}
