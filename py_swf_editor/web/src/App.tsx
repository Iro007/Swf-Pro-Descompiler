import {
  ChangeEvent,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowRight,
  BarChart3,
  Box,
  Check,
  ChevronRight,
  Code2,
  File,
  FileCode2,
  FolderOpen,
  History,
  Info,
  Layers,
  LayoutDashboard,
  Loader2,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Redo2,
  ShieldCheck,
  Undo2,
  Upload,
  X,
} from "lucide-react";
import {
  ClassInfo,
  downloadFile,
  Engine,
  EngineStatus,
  exportAllResources,
  getClassHierarchy,
  getEngineStatus,
  getHistory,
  getTags,
  ProjectHistory,
  redo,
  undo,
  uploadProject,
  uploadSwf,
} from "./api";
import { FileInfo, TagInfo } from "./types";
import ResourceExplorer from "./components/ResourceExplorer";
import HistoryPanel from "./components/HistoryPanel";
import TagPreview from "./components/TagPreview";
import Dialog from "./components/Dialog";
import ExportPanel from "./components/ExportPanel";
import ClassSourceDialog from "./components/ClassSourceDialog";
import {
  errorMessage,
  formatBytes,
  GROUPS,
  groupFor,
  saveBlob,
} from "./utils/workspace";

const StageViewer = lazy(() => import("./components/StageViewer"));
const BenchmarkPage = lazy(() => import("./components/BenchmarkPage"));
const EMPTY_HISTORY: ProjectHistory = {
  undo: 0,
  redo: 0,
  dirty: false,
  snapshots: [],
  entries: [],
};

export default function App() {
  const [benchmarksOpen, setBenchmarksOpen] = useState(() => window.location.hash === "#benchmarks");
  useEffect(() => {
    const onHashChange = () => setBenchmarksOpen(window.location.hash === "#benchmarks");
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  const [file, setFile] = useState<FileInfo | null>(null);
  const [tags, setTags] = useState<TagInfo[]>([]);
  const [selected, setSelected] = useState("overview");
  const [tab, setTab] = useState("preview");
  const [history, setHistory] = useState<ProjectHistory>(EMPTY_HISTORY);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const [engine, setEngine] = useState<Engine>("native");
  const [engines, setEngines] = useState<EngineStatus[]>([]);
  const [connection, setConnection] = useState("connecting");
  const [inspectorOpen, setInspectorOpen] = useState(
    () => window.innerWidth > 1200,
  );
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [inspectorTab, setInspectorTab] = useState("info");
  const [dialog, setDialog] = useState<"export" | "classes" | null>(null);
  const [classes, setClasses] = useState<ClassInfo[]>([]);
  const [classSourceOpen,setClassSourceOpen]=useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const projectInput = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const opening = useRef(false);
  const sid = file?.session_id ?? "";
  const selectedTag = selected.startsWith("tag-")
    ? tags.find((tag) => tag.index === Number(selected.slice(4)))
    : undefined;
  const selectedGroup = selected.startsWith("group-")
    ? GROUPS.find((group) => group.id === selected.slice(6))
    : undefined;
  const groupTags = selectedGroup
    ? tags.filter((tag) => groupFor(tag.code).id === selectedGroup.id)
    : tags;
  const jpexsAvailable = engines.some(
    (item) => item.name === "jpexs" && item.available,
  );
  const warnCount = tags.filter((tag) => tag.parse_error).length;
  const resourceSize = tags.reduce((bytes, tag) => bytes + tag.size, 0);
  const reportError = useCallback(
    (text: string) => setNotice({ text, error: true }),
    [],
  );

  useEffect(() => {
    let active = true;
    getEngineStatus()
      .then((data) => {
        if (active) {
          setEngines(data.backends);
          setConnection("ready");
        }
      })
      .catch(() => {
        if (active) setConnection("offline");
      });
    return () => {
      active = false;
    };
  }, []);

  const refreshHistory = useCallback(async () => {
    if (sid) setHistory(await getHistory(sid));
  }, [sid]);
  const refreshWork = useCallback(async () => {
    if (!sid) return;
    const [newTags, newHistory] = await Promise.all([
      getTags(sid),
      getHistory(sid),
    ]);
    setTags(newTags);
    setHistory(newHistory);
    setRevision((value) => value + 1);
  }, [sid]);
  const changed = useCallback(() => {
    void refreshWork().catch((error) => reportError(errorMessage(error)));
  }, [refreshWork, reportError]);

  async function perform(label: string, task: () => Promise<void>) {
    if (busy) return;
    setBusy(label);
    setNotice(null);
    try {
      await task();
    } catch (error) {
      reportError(errorMessage(error));
    } finally {
      setBusy("");
    }
  }
  async function openFile(candidate: File) {
    if (opening.current || busy) return;
    if (!/\.(swf|pyswfproj)$/i.test(candidate.name)) {
      reportError("Selecciona un archivo .swf o .pyswfproj.");
      return;
    }
    opening.current = true;
    await perform("Abriendo archivo…", async () => {
      const info = /\.pyswfproj$/i.test(candidate.name)
        ? await uploadProject(candidate)
        : await uploadSwf(candidate);
      const [newTags, newHistory] = await Promise.all([
        getTags(info.session_id),
        getHistory(info.session_id),
      ]);
      setFile(info);
      setTags(newTags);
      setHistory(newHistory);
      setSelected("overview");
      setTab("preview");
      setRevision((value) => value + 1);
      setInspectorTab("info");
      setNotice({
        text: info.filename + " · " + newTags.length + " tags cargados",
        error: false,
      });
    });
    opening.current = false;
  }
  async function createdFromSource(info:FileInfo) {
    await perform("Cargando programa compilado…",async()=>{
      const [nextTags,nextHistory]=await Promise.all([getTags(info.session_id),getHistory(info.session_id)]);
      setFile(info);setTags(nextTags);setHistory(nextHistory);
      setSelected("tag-1");setTab("preview");setRevision(value=>value+1);
      setClassSourceOpen(false);
      setNotice({text:info.filename+" · compilado en Python",error:false});
    });
  }
  const select = useCallback((id: string) => {
    setSelected(id);
    setTab("preview");
    setLibraryOpen(false);
  }, []);
  async function save(kind: "swf" | "project") {
    if (!file) return;
    await perform(
      kind === "swf" ? "Exportando SWF…" : "Guardando proyecto…",
      async () => {
        saveBlob(
          await downloadFile(sid, kind),
          kind === "swf"
            ? file.filename
            : file.filename.replace(/\.swf$/i, "") + ".pyswfproj",
        );
        setNotice({
          text:
            kind === "swf"
              ? "SWF exportado. El original está conservado."
              : "Proyecto guardado con original, trabajo e historial.",
          error: false,
        });
      },
    );
  }
  async function undoWork() {
    if (!sid || !history.undo) return;
    await perform("Deshaciendo…", async () => {
      await undo(sid);
      await refreshWork();
    });
  }
  async function redoWork() {
    if (!sid || !history.redo) return;
    await perform("Rehaciendo…", async () => {
      await redo(sid);
      await refreshWork();
    });
  }
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const editing = (event.target as HTMLElement).closest(
        "input, textarea, select, [contenteditable=true]",
      );
      const key = event.key.toLowerCase();
      if (key === "o") {
        event.preventDefault();
        if (!busy) input.current?.click();
      } else if (key === "s" && !editing) {
        event.preventDefault();
        void save(event.shiftKey ? "project" : "swf");
      } else if (!editing && key === "z") {
        event.preventDefault();
        void (event.shiftKey ? redoWork() : undoWork());
      } else if (!editing && key === "y") {
        event.preventDefault();
        void redoWork();
      }
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [sid, busy, history.undo, history.redo, refreshWork, file]);
  function inputChange(event: ChangeEvent<HTMLInputElement>) {
    const candidate = event.target.files?.[0];
    if (candidate) void openFile(candidate);
    event.target.value = "";
  }

  return (
    <>
    <div
      className="studio"
      hidden={benchmarksOpen}
      onDragEnter={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes("Files")) event.preventDefault();
      }}
      onDragLeave={(event) => {
        event.preventDefault();
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (!dragDepth.current) setDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        dragDepth.current = 0;
        const candidate = event.dataTransfer.files[0];
        if (candidate) void openFile(candidate);
      }}
    >
      <input
        ref={input}
        type="file"
        accept=".swf,.pyswfproj"
        aria-label="Abrir archivo SWF"
        className="sr-only"
        onChange={inputChange}
      />
      <input
        ref={projectInput}
        type="file"
        accept=".pyswfproj"
        aria-label="Abrir proyecto"
        className="sr-only"
        onChange={inputChange}
      />
      <header className="studio-header">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            select("overview");
          }}
          aria-label="PySWF Studio · inicio"
        >
          <span className="brand-mark">
            <Layers size={23} />
          </span>
          <span>
            PySWF <strong>Studio</strong>
            <small>FLASH WORKSPACE</small>
          </span>
        </a>
        <div className="document-title">
          {file ? (
            <>
              <File size={14} />
              <span title={file.filename}>{file.filename}</span>
              <span
                className={history.dirty ? "dirty-dot" : "clean-dot"}
                title={history.dirty ? "Trabajo modificado" : "Sin cambios"}
              />
            </>
          ) : (
            <span>Espacio de trabajo</span>
          )}
        </div>
        <div className="header-actions">
          <button className="studio-button secondary" disabled={Boolean(busy)} onClick={()=>setClassSourceOpen(true)}><Code2 size={16}/><span>Nuevo desde código</span></button>
          <button
            className="studio-button secondary"
            disabled={Boolean(busy)}
            onClick={() => input.current?.click()}
          >
            <FolderOpen size={16} />
            <span>Abrir archivo</span>
            <kbd>Ctrl O</kbd>
          </button>
          <button
            className="studio-button primary"
            disabled={!file || Boolean(busy)}
            onClick={() => void save("project")}
          >
            <ArrowDownToLine size={15} />
            <span>Guardar proyecto</span>
          </button>
        </div>
      </header>
      <div className="studio-toolbar">
        <div className="toolbar-left">
          <span className="eyebrow">WORKSPACE</span>
          <ChevronRight size={12} />
          <span>
            {file
              ? selectedTag?.symbol_name ||
                selectedTag?.name ||
                selectedGroup?.label ||
                "Vista general"
              : "Bienvenido"}
          </span>
        </div>
        <div className="toolbar-actions">
          <button
            className="icon-button"
            aria-label="Deshacer"
            title="Deshacer · Ctrl Z"
            disabled={!history.undo || Boolean(busy)}
            onClick={() => void undoWork()}
          >
            <Undo2 size={16} />
          </button>
          <button
            className="icon-button"
            aria-label="Rehacer"
            title="Rehacer · Ctrl Y"
            disabled={!history.redo || Boolean(busy)}
            onClick={() => void redoWork()}
          >
            <Redo2 size={16} />
          </button>
          <span className="toolbar-divider" />
          <label className="engine-select">
            <Code2 size={14} />
            <select
              aria-label="Motor predeterminado"
              value={engine}
              onChange={(event) => setEngine(event.target.value as Engine)}
            >
              <option value="auto">Motor automático</option>
              <option value="native">PySWF</option>
              <option value="jpexs" disabled={!jpexsAvailable}>
                JPEXS{!jpexsAvailable ? " · no disponible" : ""}
              </option>
              <option value="compare">Comparar motores</option>
            </select>
          </label>
          <button
            className="icon-button"
            aria-label={
              inspectorOpen ? "Ocultar inspector" : "Mostrar inspector"
            }
            onClick={() => setInspectorOpen((value) => !value)}
          >
            {inspectorOpen ? (
              <PanelRightClose size={17} />
            ) : (
              <PanelRightOpen size={17} />
            )}
          </button>
        </div>
      </div>
      {notice && (
        <div
          className={
            "notice " + (notice.error ? "notice-error" : "notice-success")
          }
          role={notice.error ? "alert" : "status"}
        >
          {notice.error ? <AlertTriangle size={16} /> : <Check size={16} />}
          <span>{notice.text}</span>
          <button
            className="icon-button"
            aria-label="Cerrar aviso"
            onClick={() => setNotice(null)}
          >
            <X size={15} />
          </button>
        </div>
      )}
      {busy && (
        <div className="busy-bar" role="status">
          <Loader2 size={14} className="animate-spin" />
          {busy}
        </div>
      )}
      <div
        className={
          "studio-body " +
          (inspectorOpen ? "with-inspector " : "") +
          (libraryOpen ? "library-open" : "")
        }
      >
        <nav className="activity-rail" aria-label="Herramientas">
          <button
            className={selected === "overview" ? "active" : ""}
            aria-label="Vista general"
            title="Vista general"
            onClick={() => select("overview")}
          >
            <LayoutDashboard size={21} />
          </button>
          <button
            aria-label="Biblioteca de recursos"
            title="Biblioteca de recursos"
            onClick={() => {
              setLibraryOpen((value) => !value);
              window.setTimeout(
                () =>
                  document
                    .querySelector<HTMLInputElement>(
                      '[aria-label="Buscar recursos"]',
                    )
                    ?.focus(),
                0,
              );
            }}
          >
            <Box size={21} />
          </button>
          <button
            disabled={!file}
            aria-label="Historial y snapshots"
            title="Historial y snapshots"
            className={
              inspectorOpen && inspectorTab === "history" ? "active" : ""
            }
            onClick={() => {
              setInspectorOpen(true);
              setInspectorTab("history");
            }}
          >
            <History size={21} />
          </button>
          <button
            disabled={!file || Boolean(busy)}
            aria-label="Exportar recursos"
            title="Exportar recursos"
            onClick={() => setDialog("export")}
          >
            <Upload size={21} />
          </button>
          <button
            disabled={!file || Boolean(busy)}
            aria-label="Clases ActionScript"
            title="Clases ActionScript"
            onClick={() =>
              void perform("Cargando clases…", async () => {
                setClasses((await getClassHierarchy(sid)).classes);
                setDialog("classes");
              })
            }
          >
            <FileCode2 size={21} />
          </button>
          <div className="rail-bottom">
            <button aria-label="Benchmarks de decompilación" title="Benchmarks de decompilación" onClick={() => { window.location.hash = "benchmarks"; setBenchmarksOpen(true); }}>
              <BarChart3 size={21} />
            </button>
            <span className="rail-version">0.1</span>
          </div>
        </nav>
        <ResourceExplorer
          key={sid || "empty"}
          sid={sid}
          tags={tags}
          selected={selected}
          onSelect={select}
          onError={reportError}
        />
        <main
          className="studio-main"
          aria-label="Área de trabajo"
          aria-busy={Boolean(busy)}
        >
          {!file ? (
            <div className="welcome">
              <span className="welcome-label">
                <span className="status-dot" />
                TU LABORATORIO FLASH
              </span>
              <h1>
                Abre el archivo.
                <br />
                <span>Descubre lo que hay dentro.</span>
              </h1>
              <p>
                Explora recursos, entiende el código y edita tu SWF desde un
                solo espacio de trabajo.
              </p>
              <button
                className="drop-zone"
                onClick={() => input.current?.click()}
                disabled={Boolean(busy)}
              >
                <span className="drop-icon">
                  <FolderOpen size={29} />
                </span>
                <strong>Arrastra un SWF aquí</strong>
                <span>o haz clic para elegir un archivo</span>
                <small>
                  .swf · .pyswfproj <span>FWS / CWS / ZWS</span>
                </small>
              </button>
              <div className="welcome-bottom">
                <button
                  className="text-button"
                  disabled={Boolean(busy)}
                  onClick={() => projectInput.current?.click()}
                >
                  <Plus size={15} />
                  Abrir un proyecto existente
                  <ArrowRight size={14} />
                </button>
                <span>
                  <ShieldCheck size={14} />
                  Original conservado en tu proyecto
                </span>
              </div>
              <div className="feature-strip">
                <div>
                  <Box size={19} />
                  <strong>Recursos</strong>
                  <span>Vectores, imágenes y audio</span>
                </div>
                <div>
                  <Code2 size={19} />
                  <strong>ActionScript</strong>
                  <span>Fuente y bytecode editable</span>
                </div>
                <div>
                  <History size={19} />
                  <strong>Historial</strong>
                  <span>Snapshots y puntos de retorno</span>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="document-tab">
                <FileCode2 size={15} />
                <span>
                  {selectedTag
                    ? selectedTag.name
                    : selectedGroup?.label || file.filename}
                </span>
                {selectedTag && <small>#{selectedTag.index}</small>}
                <span className="tab-line" />
              </div>
              <div className="main-content">
                {selectedTag ? (
                  <>
                    <div className="resource-heading">
                      <div>
                        <span className="eyebrow">
                          {groupFor(selectedTag.code).label}
                        </span>
                        <h1>{selectedTag.symbol_name || selectedTag.name}</h1>
                        <p>
                          Tag #{selectedTag.index} <span>·</span>{" "}
                          {formatBytes(selectedTag.size)}{" "}
                          {selectedTag.char_id != null && (
                            <>
                              <span>·</span> Character #{selectedTag.char_id}
                            </>
                          )}
                        </p>
                      </div>
                      <div className="segmented" aria-label="Vista del recurso">
                        <button
                          aria-pressed={tab === "preview"}
                          onClick={() => setTab("preview")}
                        >
                          Vista previa
                        </button>
                        <button
                          aria-pressed={tab === "hex"}
                          onClick={() => setTab("hex")}
                        >
                          Hexadecimal
                        </button>
                      </div>
                    </div>
                    {selectedTag.parse_error && (
                      <p className="inline-warning">
                        <AlertTriangle size={16} />
                        {selectedTag.parse_error}
                      </p>
                    )}
                    <TagPreview
                      key={sid + "-" + selectedTag.index + "-" + revision}
                      sid={sid}
                      tag={selectedTag}
                      hex={tab === "hex"}
                      engine={engine}
                      onChanged={changed}
                      onError={reportError}
                      onHex={() => setTab("hex")}
                    />
                  </>
                ) : (
                  <>
                    <div className="overview-heading">
                      <div>
                        <span className="eyebrow">
                          {selectedGroup ? "BIBLIOTECA" : "ARCHIVO ABIERTO"}
                        </span>
                        <h1>{selectedGroup?.label || file.filename}</h1>
                        <p>
                          {selectedGroup
                            ? groupTags.length + " recursos en esta categoría"
                            : "Explora, previsualiza y edita tu archivo Flash."}
                        </p>
                      </div>
                      <span className="file-badge">
                        {file.signature}
                        <span>SWF {file.version}</span>
                      </span>
                    </div>
                    {!selectedGroup && (
                      <>
                        <div className="metric-row">
                          <div>
                            <span>ESCENARIO</span>
                            <strong>
                              {file.width} × {file.height}
                              <small>px</small>
                            </strong>
                          </div>
                          <div>
                            <span>TIMELINE</span>
                            <strong>
                              {file.frame_count}
                              <small>frames</small>
                            </strong>
                          </div>
                          <div>
                            <span>VELOCIDAD</span>
                            <strong>
                              {file.frame_rate}
                              <small>fps</small>
                            </strong>
                          </div>
                          <div>
                            <span>RECURSOS</span>
                            <strong>{formatBytes(resourceSize)}</strong>
                          </div>
                        </div>
                        <div className="overview-stage">
                          <Suspense
                            fallback={
                              <div className="viewer-loading">
                                Preparando escenario…
                              </div>
                            }
                          >
                            <StageViewer
                              key={sid + "-" + revision}
                              sid={sid}
                              onError={reportError}
                            />
                          </Suspense>
                        </div>
                        <div className="section-heading">
                          <h2>Biblioteca del archivo</h2>
                          <span>{tags.length} tags</span>
                        </div>
                        <div className="category-grid">
                          {GROUPS.map((group) => {
                            const count = tags.filter(
                              (tag) => groupFor(tag.code).id === group.id,
                            ).length;
                            return count > 0 ? (
                              <button
                                key={group.id}
                                onClick={() => select("group-" + group.id)}
                              >
                                <Box size={16} />
                                <span>{group.label}</span>
                                <strong>{count}</strong>
                                <ChevronRight size={14} />
                              </button>
                            ) : null;
                          })}
                        </div>
                      </>
                    )}
                    <div className="section-heading">
                      <h2>{selectedGroup ? "Recursos" : "Estructura SWF"}</h2>
                      <span>Selecciona un tag para inspeccionarlo</span>
                    </div>
                    <div className="tag-table-wrap">
                      <table className="tag-table">
                        <thead>
                          <tr>
                            <th>TAG</th>
                            <th>RECURSO</th>
                            <th>ID</th>
                            <th>TAMAÑO</th>
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {groupTags.slice(0, 500).map((tag) => (
                            <tr key={tag.index}>
                              <td className="mono">#{tag.index}</td>
                              <td>
                                <button
                                  onClick={() => select("tag-" + tag.index)}
                                >
                                  <span
                                    className={
                                      "type-icon type-" + groupFor(tag.code).id
                                    }
                                  >
                                    <FileCode2 size={14} />
                                  </span>
                                  <span>
                                    <strong>
                                      {tag.symbol_name || tag.name}
                                    </strong>
                                    {tag.symbol_name && (
                                      <small>{tag.name}</small>
                                    )}
                                  </span>
                                  {tag.parse_error && (
                                    <AlertTriangle
                                      size={13}
                                      className="warning"
                                    />
                                  )}
                                </button>
                              </td>
                              <td className="mono">{tag.char_id ?? "—"}</td>
                              <td className="mono">{formatBytes(tag.size)}</td>
                              <td>
                                <button
                                  className="icon-button"
                                  aria-label={"Abrir tag " + tag.index}
                                  onClick={() => select("tag-" + tag.index)}
                                >
                                  <ChevronRight size={14} />
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {groupTags.length > 500 && (
                        <p className="muted table-note">
                          Primeros 500 tags. Usa el explorador para acceder al
                          resto.
                        </p>
                      )}
                    </div>
                  </>
                )}
              </div>
            </>
          )}
        </main>
        {inspectorOpen && (
          <aside className="studio-inspector" aria-label="Inspector">
            <div className="inspector-tabs">
              <button
                aria-pressed={inspectorTab === "info"}
                onClick={() => setInspectorTab("info")}
              >
                <Info size={14} />
                Inspector
              </button>
              <button
                aria-pressed={inspectorTab === "history"}
                onClick={() => setInspectorTab("history")}
              >
                <History size={14} />
                Historial
              </button>
            </div>
            <div className="inspector-content">
              {inspectorTab === "history" && file ? (
                <HistoryPanel
                  key={sid + "-" + revision}
                  sid={sid}
                  history={history}
                  onRefresh={refreshHistory}
                  onRestored={refreshWork}
                  onSelectTag={(index) => select("tag-" + index)}
                  onError={reportError}
                />
              ) : (
                <>
                  <div className="inspector-title">
                    <Info size={17} />
                    <h3>
                      {selectedTag
                        ? "Propiedades del recurso"
                        : "Propiedades del archivo"}
                    </h3>
                  </div>
                  {file ? (
                    <dl className="properties">
                      {(selectedTag
                        ? [
                            ["Tipo", selectedTag.name],
                            ["Índice", "#" + selectedTag.index],
                            ["Código", selectedTag.code],
                            ["Character ID", selectedTag.char_id ?? "—"],
                            ["Tamaño", formatBytes(selectedTag.size)],
                            [
                              "Símbolo",
                              selectedTag.symbol_name || "Sin nombre",
                            ],
                          ]
                        : [
                            ["Archivo", file.filename],
                            ["Formato", file.signature + " · v" + file.version],
                            [
                              "Escenario",
                              file.width + " × " + file.height + " px",
                            ],
                            ["Velocidad", file.frame_rate + " fps"],
                            ["Frames", file.frame_count],
                            ["Tags", tags.length],
                          ]
                      ).map(([label, value]) => (
                        <div key={label}>
                          <dt>{label}</dt>
                          <dd>{value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="muted">
                      Abre un SWF para ver sus propiedades y recursos.
                    </p>
                  )}
                  <div className="section-caption">MOTORES DE CÓDIGO</div>
                  <div className="engine-card">
                    <span className="status-dot" />
                    <strong>PySWF</strong>
                    <small>Fuente y P-code</small>
                  </div>
                  <div className="engine-card">
                    <span
                      className={
                        jpexsAvailable ? "status-dot" : "unavailable-dot"
                      }
                    />
                    <strong>JPEXS</strong>
                    <small>
                      {jpexsAvailable ? "Disponible" : "Sin configurar"}
                    </small>
                  </div>
                  <p className="muted small">
                    Python abre, edita y guarda sin Java. JPEXS es una opción
                    adicional solo si lo seleccionas.
                  </p>
                  <div className="original-note">
                    <ShieldCheck size={19} />
                    <div>
                      <strong>Trabaja con un punto de retorno</strong>
                      <p>
                        Guarda un snapshot desde Historial antes de editar.
                        Inclúyelo al descargar tu proyecto.
                      </p>
                    </div>
                  </div>
                  {file && (
                    <>
                      <div className="section-caption">EXPORTACIÓN</div>
                      <button
                        className="studio-button secondary full-width"
                        disabled={Boolean(busy)}
                        onClick={() => void save("swf")}
                      >
                        <ArrowDownToLine size={14} />
                        Exportar SWF
                      </button>
                      <button
                        className="text-button full-width"
                        disabled={Boolean(busy)}
                        onClick={() => setDialog("export")}
                      >
                        Exportar recursos <ArrowRight size={14} />
                      </button>
                    </>
                  )}
                </>
              )}
            </div>
          </aside>
        )}
      </div>
      <footer className="studio-status">
        <span>
          <span
            className={
              connection === "ready" ? "status-dot" : "unavailable-dot"
            }
          />
          {connection === "ready"
            ? "Servidor conectado"
            : connection === "connecting"
              ? "Conectando…"
              : "Servidor no disponible"}
        </span>
        <span>
          {file
            ? history.dirty
              ? "Trabajo modificado"
              : "Sin cambios en el original"
            : "Listo para abrir un archivo"}
        </span>
        {warnCount > 0 && (
          <span className="warning">
            <AlertTriangle size={12} />
            {warnCount} advertencias
          </span>
        )}
        <span className="status-right">
          {file
            ? tags.length + " tags · " + formatBytes(resourceSize)
            : "SWF · AS1 / AS2 / AS3"}
          <kbd>Ctrl Shift S</kbd>Proyecto
        </span>
      </footer>
      {dragging && (
        <div className="drag-overlay">
          <Upload size={46} />
          <h2>Suelta el archivo para abrirlo</h2>
          <p>SWF o proyecto PySWF</p>
        </div>
      )}
      {dialog === "export" && file && (
        <Dialog title="Exportar recursos" onClose={() => setDialog(null)}>
          <ExportPanel
            sid={sid}
            filename={file.filename}
            tags={tags}
            onError={reportError}
            onExportAll={() =>
              void perform("Exportando recursos…", async () => {
                saveBlob(
                  await exportAllResources(sid),
                  file.filename + ".resources.zip",
                );
                setDialog(null);
                setNotice({
                  text: "Recursos exportados en ZIP.",
                  error: false,
                });
              })
            }
          />
        </Dialog>
      )}
      {dialog === "classes" && (
        <Dialog title="Clases ActionScript 3" onClose={() => setDialog(null)}>
          {classes.length ? (
            <div className="class-list">
              {classes.map((cls, index) => (
                <div key={cls.name + "-" + index}>
                  <Code2 size={17} />
                  <strong>{cls.name}</strong>
                  <small>
                    {cls.super ? "extends " + cls.super + " · " : ""}
                    {cls.methods} métodos · {cls.fields} campos
                  </small>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">
              Este archivo no contiene clases ActionScript 3.
            </p>
          )}
        </Dialog>
      )}
      {classSourceOpen && (
        <ClassSourceDialog
          onClose={() => setClassSourceOpen(false)}
          onCreated={createdFromSource}
        />
      )}
    </div>
    {benchmarksOpen && <Suspense fallback={<div className="benchmark-state" role="status">Cargando benchmarks…</div>}>
      <BenchmarkPage onClose={() => { setBenchmarksOpen(false); if (window.location.hash === "#benchmarks") window.history.replaceState(null, "", window.location.pathname + window.location.search); }} />
    </Suspense>}
    </>
  );
}
