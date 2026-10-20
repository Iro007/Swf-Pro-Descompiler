import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Code2,
  Copy,
  Edit3,
  GitCompareArrows,
  Loader2,
  RotateCcw,
  Save,
  Search,
} from "lucide-react";
import {
  assemble,
  ApiError,
  compileSource,
  compileDocument,
  Decompilation,
  DecompileSection,
  Engine,
  EngineOutput,
  getDecompilation,
  getDisassembly,
} from "../api";
import { ScriptListing } from "../types";
import { highlightCode, renderHighlighted } from "../utils/syntaxHighlight";
import { errorMessage } from "../utils/workspace";

interface Props {
  sid: string;
  tagIndex: number;
  tagName: string;
  onChanged: () => void;
  onError: (message: string) => void;
  initialEngine?: Engine;
}

function CodePane({ text, query = "" }: { text: string; query?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [match, setMatch] = useState(0);
  const [limit, setLimit] = useState(2000);
  const lines = useMemo(() => text.split("\n"), [text]);
  const matches = useMemo(
    () =>
      query
        ? lines.flatMap((line, index) =>
            line.toLowerCase().includes(query.toLowerCase()) ? [index] : [],
          )
        : [],
    [lines, query],
  );
  useEffect(() => {
    setMatch(0);
  }, [text, query]);
  useEffect(() => {
    const line = matches[match];
    if (line === undefined) return;
    if (line >= limit) setLimit(line + 100);
    container.current
      ?.querySelector(`[data-line="${line}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [matches, match, limit]);
  return (
    <div className="code-pane">
      <div className="code-lines" ref={container}>
        {lines.slice(0, limit).map((line, index) => (
          <div
            key={index}
            data-line={index}
            className={
              "code-line " +
              (matches.includes(index) ? "matched " : "") +
              (matches[match] === index ? "current-match" : "")
            }
          >
            <span className="line-number" aria-hidden="true">
              {index + 1}
            </span>
            <code>{renderHighlighted(highlightCode(line)) || " "}</code>
          </div>
        ))}
        {lines.length > limit && (
          <button
            className="show-more"
            onClick={() => setLimit((current) => current + 2000)}
          >
            Mostrar 2000 líneas más ({lines.length - limit} restantes)
          </button>
        )}
      </div>
      <div className="code-pane-footer">
        <span>{lines.length} líneas</span>
        {query && (
          <span>
            {matches.length ? match + 1 : 0} / {matches.length} líneas
            coincidentes
            <button
              aria-label="Coincidencia anterior"
              className="icon-button"
              disabled={!matches.length}
              onClick={() =>
                setMatch(
                  (current) => (current - 1 + matches.length) % matches.length,
                )
              }
            >
              <ChevronUp size={13} />
            </button>
            <button
              aria-label="Coincidencia siguiente"
              className="icon-button"
              disabled={!matches.length}
              onClick={() =>
                setMatch((current) => (current + 1) % matches.length)
              }
            >
              <ChevronDown size={13} />
            </button>
          </span>
        )}
      </div>
    </div>
  );
}

function ComparisonPane({
  title,
  output,
  query,
}: {
  title: string;
  output?: EngineOutput;
  query: string;
}) {
  const [selected, setSelected] = useState(0);
  const sections = output?.sections ?? [];
  return (
    <section className="comparison-pane" aria-label={title}>
      <div className="comparison-heading">
        <strong>{title}</strong>
        <span className={output?.error ? "warning" : "success"}>
          {output?.error ? "Sin salida" : sections.length + " secciones"}
        </span>
      </div>
      {sections.length > 1 && (
        <select
          aria-label={"Sección " + title}
          value={selected}
          onChange={(event) => setSelected(Number(event.target.value))}
        >
          {sections.map((section, index) => (
            <option key={index} value={index}>
              {section.name}
            </option>
          ))}
        </select>
      )}
      {output?.error ? (
        <div className="comparison-unavailable">
          <AlertTriangle size={22} />
          <strong>Motor no disponible</strong>
          <p>{output.error}</p>
          <span>Puedes continuar inspeccionando la salida del otro motor.</span>
        </div>
      ) : (
        <>
          <p className="section-diagnostic">{sections[selected]?.error}</p>
          <CodePane text={sections[selected]?.source ?? ""} query={query} />
        </>
      )}
    </section>
  );
}

export default function ScriptViewer({
  sid,
  tagIndex,
  tagName,
  onChanged,
  onError,
  initialEngine = "native",
}: Props) {
  const [mode, setMode] = useState<"source" | "pcode">("source");
  const [engine, setEngine] = useState<Engine>(initialEngine);
  const [output, setOutput] = useState<Decompilation | null>(null);
  const [listing, setListing] = useState<ScriptListing | null>(null);
  const [selected, setSelected] = useState(0);
  const [body, setBody] = useState(0);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [conflict,setConflict] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const section: DecompileSection | undefined = output?.sections?.[selected];
  const original =
    mode === "pcode"
      ? (listing?.scripts[body]?.code ?? "")
      : (section?.source ?? "");
  const dirty = (editing || mode === "pcode") && draft !== original;
  const programDocument = section?.source_document?.kind === "script";
  const editable =
    output?.engine === "native" &&
    !section?.error &&
    section?.editable_source !== false &&
    (section?.body_index !== undefined || output?.kind === "avm1" || programDocument);
  const replaceable =
    output?.engine === "native" && section?.replaceable_source === true;
  const reconstructed = section?.reconstruction === "structured";
  const compilable = editable || replaceable;
  const text = editing || mode === "pcode" ? draft : original;
  const clipboardTimer = useRef<number | undefined>(undefined);
  const preferredBody = useRef<number | null>(null);

  useEffect(() => {
    setEngine(initialEngine);
  }, [initialEngine]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setConflict(false);
    setOutput(null);
    setListing(null);
    setEditing(false);
    setDraft("");
    setSelected(0);
    setBody(0);
    const request =
      mode === "source"
        ? getDecompilation(sid, tagIndex, engine, controller.signal)
        : getDisassembly(sid, tagIndex);
    request
      .then((data) => {
        if (controller.signal.aborted) return;
        if (mode === "source") {
          const next = data as Decompilation;
          setOutput(next);
          const index =
            preferredBody.current === null
              ? Math.max(0, next.sections?.findIndex(item => item.source_document?.kind === "script") ?? 0)
              : (next.sections?.findIndex(
                  (item) => item.body_index === preferredBody.current,
                ) ?? 0);
          setSelected(Math.max(0, index));
        } else {
          const next = data as ScriptListing;
          setListing(next);
          const index = Math.max(
            0,
            next.scripts.findIndex(
              (item) => item.body_index === (preferredBody.current ?? 0),
            ),
          );
          setBody(index);
          setDraft(next.scripts[index]?.code ?? "");
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [sid, tagIndex, engine, mode]);
  useEffect(() => () => window.clearTimeout(clipboardTimer.current), []);

  async function apply() {
    if (saving || !dirty || conflict) return;
    setSaving(true);
    try {
      if (mode === "pcode" && listing)
        await assemble(sid, tagIndex, listing.scripts[body].body_index, draft);
      else if (compilable) {
        if (section?.source_document)
          await compileDocument(sid,tagIndex,section.source_document,draft);
        else
          await compileSource(sid, tagIndex, section?.body_index ?? 0, draft);
      }
      else
        throw new Error("Esta sección no corresponde a un método compilable.");
      onChanged();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status===409) setConflict(true);
      onError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }
  async function discardAndReload() {
    setLoading(true);
    try {
      const next=await getDecompilation(sid,tagIndex,engine);
      const index=next.sections?.findIndex(item=>item.source_document?.document_id===section?.source_document?.document_id) ?? 0;
      setOutput(next);
      setSelected(Math.max(0,index));
      setDraft("");
      setEditing(false);
      setConflict(false);
    } catch(reason) {
      onError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.clearTimeout(clipboardTimer.current);
      clipboardTimer.current = window.setTimeout(() => setCopied(false), 1500);
    } catch (reason) {
      onError("No se pudo copiar: " + errorMessage(reason));
    }
  }

  return (
    <div className="script-workspace">
      <div className="script-toolbar">
        <span className="script-label">
          <Code2 size={16} />
          {tagName}
        </span>
        <div className="script-mode">
          <button
            disabled={dirty || saving}
            aria-pressed={mode === "source"}
            onClick={() => setMode("source")}
          >
            Código fuente
          </button>
          <button
            disabled={dirty || saving}
            aria-pressed={mode === "pcode"}
            onClick={() => {
              if (section?.body_index !== undefined)
                preferredBody.current = section.body_index;
              setMode("pcode");
            }}
          >
            P-code
          </button>
        </div>
        {mode === "source" && (
          <select
            aria-label="Motor de decompilación"
            value={engine}
            disabled={dirty || saving}
            onChange={(event) => setEngine(event.target.value as Engine)}
          >
            <option value="auto">Automático</option>
            <option value="native">Python</option>
            <option value="jpexs">JPEXS</option>
            <option value="compare">Comparar motores</option>
          </select>
        )}
        <div className="script-search">
          <Search size={14} />
          <input
            aria-label="Buscar en el código"
            placeholder="Buscar en el código…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <button
          className="icon-button"
          aria-label={copied ? "Código copiado" : "Copiar código"}
          disabled={loading || !text}
          onClick={() => void copy()}
        >
          {copied ? <Check size={15} /> : <Copy size={15} />}
        </button>
      </div>
      {error ? (
        <div className="script-error" role="alert">
          <AlertTriangle size={22} />
          <h3>No se pudo obtener el código</h3>
          <p>{error}</p>
          <button
            className="studio-button secondary"
            onClick={() => {
              setEngine("native");
              setMode("pcode");
            }}
          >
            Abrir P-code nativo
          </button>
        </div>
      ) : loading ? (
        <div className="viewer-loading" role="status">
          <Loader2 size={24} className="animate-spin" />
          Obteniendo código…
        </div>
      ) : mode === "source" && engine === "compare" && output ? (
        <>
          <div className="comparison-banner">
            <GitCompareArrows size={15} />
            <span>Python ↔ JPEXS · comparación de salida</span>
            {output.jpexs?.ok && output.metrics && (
              <small>
                {Math.round(output.metrics.matching_line_ratio * 100)}% de
                líneas coincidentes · no mide equivalencia del código
              </small>
            )}
          </div>
          <div className="comparison-grid">
            <ComparisonPane
              title="PySWF"
              output={output.native}
              query={query}
            />
            <ComparisonPane title="JPEXS" output={output.jpexs} query={query} />
          </div>
        </>
      ) : (
        <>
          <div className="script-section-bar">
            <label>
              {mode === "pcode" ? "Método" : "Sección"}
              <select
                aria-label={
                  mode === "pcode" ? "Método P-code" : "Sección de fuente"
                }
                disabled={dirty || saving}
                value={mode === "pcode" ? body : selected}
                onChange={(event) => {
                  const index = Number(event.target.value);
                  setEditing(false);
                  if (mode === "pcode") {
                    setBody(index);
                    preferredBody.current =
                      listing?.scripts[index]?.body_index ?? null;
                    setDraft(listing?.scripts[index]?.code ?? "");
                  } else {
                    setSelected(index);
                    preferredBody.current =
                      output?.sections?.[index]?.body_index ?? null;
                  }
                }}
              >
                {(mode === "pcode" ? listing?.scripts : output?.sections)?.map(
                  (item, index) => (
                    <option key={index} value={index}>
                      {item.name}
                    </option>
                  ),
                )}
              </select>
            </label>
            <span className="source-engine">
              {mode === "pcode" ? listing?.kind : output?.engine}
            </span>
            {mode === "source" && !editing && (
              <button
                className="text-button"
                disabled={!compilable}
                title={
                  editable
                    ? programDocument ? "Editar el programa AS3 original verificado" : "Editar este método"
                    : replaceable
                      ? reconstructed
                        ? "Escribir un cuerpo nuevo. Esta fuente se reconstruyó, pero el compilador nativo aún no admite su sintaxis."
                        : "Escribir un cuerpo nuevo sin reutilizar la decompilación aproximada"
                      : section?.source_edit_reason ||
                        "Esta sección es de lectura. Usa P-code para editar bytecode."
                }
                onClick={() => {
                  setDraft(editable ? original : "");
                  setEditing(true);
                }}
              >
                <Edit3 size={14} />
                {editable
                  ? "Editar fuente"
                  : replaceable
                    ? "Reemplazar cuerpo AS"
                    : "Editar fuente"}
              </button>
            )}
            {mode === "source" && !editing && compilable && section?.saved_source !== undefined && (
              <button className="text-button" onClick={() => {
                setDraft(section.saved_source ?? "");
                setEditing(true);
              }}>
                <RotateCcw size={13} />Recuperar fuente guardada
              </button>
            )}
            {(editing || mode === "pcode") && (
              <>
                <button
                  className="text-button"
                  disabled={saving}
                  onClick={() => {
                    setDraft(original);
                    setEditing(false);
                  }}
                >
                  <RotateCcw size={13} />
                  Descartar
                </button>
                <button
                  className="studio-button primary"
                  disabled={saving || !dirty || conflict}
                  onClick={() => void apply()}
                >
                  <Save size={14} />
                  {saving
                    ? "Aplicando…"
                    : mode === "pcode"
                      ? "Ensamblar"
                      : "Recompilar"}
                </button>
              </>
            )}
          </div>
          {section?.error && mode === "source" && (
            <p className="inline-warning">
              <AlertTriangle size={14} />
              {section.error}
            </p>
          )}
          {conflict && (
            <p className="inline-warning" role="alert">
              <AlertTriangle size={14} />El archivo cambió. Tu borrador sigue intacto y no se ha aplicado.
              <button className="text-button" disabled={saving} onClick={() => void discardAndReload()}>Descartar borrador y recargar</button>
            </p>
          )}
          {mode === "source" && section?.source_document && (
            <p className="section-diagnostic" data-testid="source-document-identity">
              {section.source_document.kind === "outline" ? "Esquema de lectura" : programDocument ? "Programa AS3 · fuente original verificada" : "Documento de " + (section.source_document.kind === "method" ? "método" : "acción")}
              {" · "}{section.source_document.document_id}
              {section.source_document.editable ? " · edición protegida frente a cambios del archivo" : " · solo lectura"}
            </p>
          )}
          {mode === "source" && programDocument && (
            <p className="section-diagnostic">
              Puedes cambiar cuerpos de métodos y valores constantes de campos. Cambios de firmas,
              nombres, herencia o estructura se rechazan sin modificar el SWF. La fuente original
              se conserva al guardar un proyecto; un SWF exportado por sí solo no la incluye.
            </p>
          )}
          {editing || mode === "pcode" ? (
            <>
              {editing && !editable && (
                <p className="inline-warning" role="status">
                  <AlertTriangle size={14} />
                  La decompilación es aproximada. Escribe el cuerpo completo del
                  método; recompilar lo reemplaza y conserva la versión anterior
                  en el historial.
                </p>
              )}
              <textarea
                aria-label={
                  mode === "pcode" ? "Editor P-code" : "Editor ActionScript"
                }
                className="source-editor"
                value={draft}
                spellCheck={false}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    (event.ctrlKey || event.metaKey) &&
                    event.key.toLowerCase() === "s"
                  ) {
                    event.preventDefault();
                    void apply();
                  }
                }}
              />
            </>
          ) : (
            <CodePane text={original} query={query} />
          )}
          <div className="script-help">
            {dirty
              ? "Cambios sin aplicar. Ensambla/recompila o descarta antes de cambiar de sección."
              : mode === "pcode"
                ? "Edita el bytecode y aplica con Ctrl S. El cambio se añade al historial."
                : editable
                  ? programDocument ? "Programa editable con el compilador nativo; Ctrl S recompila sobre una copia validada." : "Método editable con el compilador nativo."
                  : reconstructed
                    ? "Fuente reconstruida de lectura. Usa P-code: el compilador nativo aún no admite su sintaxis o sus ámbitos."
                    : replaceable
                    ? "Reconstrucción parcial. Puedes reemplazar el cuerpo completo con fuente nueva o editar P-code."
                    : "Sección de lectura. Usa P-code para editar el bytecode del método."}
          </div>
        </>
      )}
    </div>
  );
}
