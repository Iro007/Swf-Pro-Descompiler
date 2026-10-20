import { useDeferredValue, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FileCode2,
  Folder,
  LayoutDashboard,
  Loader2,
  Search,
  X,
} from "lucide-react";
import { SearchResult, searchFile } from "../api";
import { TagInfo } from "../types";
import { errorMessage, GROUPS, matchesTag } from "../utils/workspace";

interface Props {
  sid: string;
  tags: TagInfo[];
  selected: string;
  onSelect: (id: string) => void;
  onError: (message: string) => void;
}

export default function ResourceExplorer({
  sid,
  tags,
  selected,
  onSelect,
  onError,
}: Props) {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("resources");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    shapes: true,
    scripts: true,
    images: true,
  });
  const [limits, setLimits] = useState<Record<string, number>>({});
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const deferred = useDeferredValue(query.trim());
  const groups = useMemo(
    () =>
      GROUPS.map((group) => ({
        ...group,
        tags: tags.filter((tag) =>
          group.id === "other"
            ? !GROUPS.some((other) => other.codes.includes(tag.code))
            : group.codes.includes(tag.code),
        ),
      })),
    [tags],
  );

  useEffect(() => {
    if (scope !== "content" || !deferred || !sid) {
      setResults([]);
      setSearching(false);
      return;
    }
    const controller = new AbortController();
    setSearching(true);
    setResults([]);
    const timer = window.setTimeout(() => {
      searchFile(sid, deferred, controller.signal)
        .then((data) => {
          if (!controller.signal.aborted) setResults(data);
        })
        .catch((error) => {
          if (!controller.signal.aborted) onError(errorMessage(error));
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [scope, deferred, sid, onError]);

  const visibleGroups = groups.map((group) => ({
    ...group,
    matches: group.tags.filter((tag) => matchesTag(tag, deferred)),
  }));
  const matchCount = visibleGroups.reduce(
    (count, group) => count + group.matches.length,
    0,
  );
  return (
    <aside className="resource-explorer" aria-label="Explorador de recursos">
      <div className="panel-heading">
        <span>EXPLORADOR</span>
        <span className="count-pill">{tags.length}</span>
      </div>
      <div className="explorer-search">
        <Search size={15} />
        <input
          aria-label="Buscar recursos"
          placeholder="Nombre, símbolo o ID…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {query && (
          <button
            className="icon-button"
            aria-label="Limpiar búsqueda"
            onClick={() => setQuery("")}
          >
            <X size={13} />
          </button>
        )}
      </div>
      <div className="search-scope">
        <button
          aria-pressed={scope === "resources"}
          onClick={() => setScope("resources")}
        >
          Recursos
        </button>
        <button
          aria-pressed={scope === "content"}
          onClick={() => setScope("content")}
        >
          Contenido AS / texto
        </button>
      </div>
      <nav className="explorer-list" aria-label="Recursos del SWF">
        <button
          className={`tree-overview ${selected === "overview" ? "selected" : ""}`}
          onClick={() => onSelect("overview")}
        >
          <LayoutDashboard size={16} />
          <span>Vista general</span>
        </button>
        {!sid ? (
          <div className="explorer-empty">
            <Folder size={30} />
            <p>Tu biblioteca aparecerá aquí al abrir un archivo.</p>
          </div>
        ) : scope === "content" && deferred ? (
          <div aria-live="polite">
            {searching ? (
              <p className="muted loading-line">
                <Loader2 size={14} className="animate-spin" /> Buscando en el
                contenido…
              </p>
            ) : (
              <p className="search-count">
                {results.length} resultados
                {results.length === 100 ? " · límite de 100" : ""}
              </p>
            )}
            {results.map((result, index) => (
              <button
                key={`${result.index}-${index}`}
                className={`search-result ${selected === `tag-${result.index}` ? "selected" : ""}`}
                onClick={() => onSelect(`tag-${result.index}`)}
              >
                <span>
                  <FileCode2 size={14} />
                  {result.name}
                  <small>#{result.index}</small>
                </span>
                <p>{result.snippet}</p>
                <small>{result.match_type}</small>
              </button>
            ))}
            {!searching && !results.length && (
              <p className="explorer-empty">
                No hay coincidencias. Prueba otra palabra.
              </p>
            )}
          </div>
        ) : (
          <>
            {deferred && (
              <p className="search-count" role="status">
                {matchCount} coincidencias
              </p>
            )}
            {visibleGroups
              .filter((group) => group.tags.length > 0)
              .map((group) => {
                if (!group.matches.length) return null;
                const open = Boolean(deferred) || expanded[group.id];
                const limit = limits[group.id] ?? 100;
                return (
                  <section key={group.id} className="resource-group">
                    <button
                      className="tree-folder"
                      aria-expanded={Boolean(open)}
                      onClick={() =>
                        setExpanded((previous) => ({
                          ...previous,
                          [group.id]: !previous[group.id],
                        }))
                      }
                    >
                      {open ? (
                        <ChevronDown size={13} />
                      ) : (
                        <ChevronRight size={13} />
                      )}
                      <Folder size={15} />
                      <span>{group.label}</span>
                      <small>{group.matches.length}</small>
                    </button>
                    {open && (
                      <div className="tree-children">
                        {group.matches.slice(0, limit).map((tag) => (
                          <button
                            key={tag.index}
                            className={`tree-tag ${selected === `tag-${tag.index}` ? "selected" : ""}`}
                            title={`${tag.name}${tag.symbol_name ? ` · ${tag.symbol_name}` : ""} · tag #${tag.index}`}
                            aria-current={
                              selected === `tag-${tag.index}`
                                ? "true"
                                : undefined
                            }
                            onClick={() => onSelect(`tag-${tag.index}`)}
                          >
                            <span className="tag-bullet" />
                            <span>{tag.symbol_name || tag.name}</span>
                            {tag.parse_error ? (
                              <AlertTriangle size={12} className="warning" />
                            ) : (
                              <small>{tag.char_id ?? `#${tag.index}`}</small>
                            )}
                          </button>
                        ))}
                        {group.matches.length > limit && (
                          <button
                            className="show-more"
                            onClick={() =>
                              setLimits((previous) => ({
                                ...previous,
                                [group.id]: limit + 100,
                              }))
                            }
                          >
                            Mostrar 100 más ({group.matches.length - limit}{" "}
                            restantes)
                          </button>
                        )}
                      </div>
                    )}
                  </section>
                );
              })}
            {deferred && !matchCount && (
              <p className="explorer-empty">
                Sin resultados para «{deferred}».
              </p>
            )}
          </>
        )}
      </nav>
      <div className="explorer-footer">
        <span className="status-dot" />
        Original protegido<span className="mono">.pyswfproj</span>
      </div>
    </aside>
  );
}
