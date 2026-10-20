import React, { useState, useEffect } from "react";
import { Check, Edit, Info, RotateCcw, Save } from "lucide-react";

interface HexEditorProps {
  tagIndex: number;
  tagName: string;
  bytes: Uint8Array;
  onUpdateHex?: (tagIndex: number, updatedBytes: Uint8Array) => Promise<void>;
}

export default function HexEditor({
  tagIndex,
  tagName,
  bytes,
  onUpdateHex,
}: HexEditorProps) {
  const [localBytes, setLocalBytes] = useState<Uint8Array>(bytes);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editingValue, setEditingValue] = useState<string>("");
  const [isApplied, setIsApplied] = useState<boolean>(false);
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState(0);
  const pageSize = 1024;

  useEffect(() => {
    setLocalBytes(new Uint8Array(bytes));
    setEditingIndex(null);
    setIsApplied(false);
    setPage(0);
  }, [bytes]);

  // Convert Uint8Array to rows of 16 bytes
  const getHexRows = () => {
    const rows = [];
    for (
      let i = page * pageSize;
      i < Math.min(localBytes.length, (page + 1) * pageSize);
      i += 16
    ) {
      const chunk: number[] = Array.from(localBytes.slice(i, i + 16));
      rows.push({
        offset: i,
        bytes: chunk,
        ascii: chunk.map((b) =>
          b >= 32 && b <= 126 ? String.fromCharCode(b) : ".",
        ),
      });
    }
    return rows;
  };

  const handleCellClick = (absIndex: number, currentValue: number) => {
    setEditingIndex(absIndex);
    setEditingValue(currentValue.toString(16).padStart(2, "0").toUpperCase());
  };

  const handleHexSubmit = (absIndex: number) => {
    // Validate hex string (00 to FF)
    const val = parseInt(editingValue, 16);
    if (/^[0-9a-f]{1,2}$/i.test(editingValue) && val >= 0 && val <= 255) {
      const newBytes = new Uint8Array(localBytes);
      newBytes[absIndex] = val;
      setLocalBytes(newBytes);
    }
    setEditingIndex(null);
  };

  const handleApply = async () => {
    if (!onUpdateHex || busy) return;
    setBusy(true);
    try {
      await onUpdateHex(tagIndex, localBytes);
      setIsApplied(true);
    } catch {
      setIsApplied(false); // The parent reports the backend error.
    } finally {
      setBusy(false);
    }
  };

  const handleReset = () => {
    setLocalBytes(new Uint8Array(bytes));
    setEditingIndex(null);
  };

  const rows = getHexRows();

  return (
    <div
      id={`hex-editor-${tagIndex}`}
      className="bg-slate-900 border border-slate-700/60 rounded-xl overflow-hidden shadow-2xl transition duration-300 hover:border-slate-600/80 flex flex-col h-[480px]"
    >
      {/* Top Section */}
      <div className="bg-slate-950/80 px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Edit className="w-4 h-4 text-purple-400" />
          <div className="flex flex-col">
            <span className="font-sans text-sm text-slate-200 font-semibold">
              Editor hexadecimal
            </span>
            <span className="text-[10px] font-mono text-slate-500 font-medium">
              Tag Index #{tagIndex} — {tagName} ({bytes.length} bytes)
            </span>
          </div>
        </div>

        <div className="flex gap-2">
          <button
            onClick={handleReset}
            disabled={busy}
            className="p-1 px-2.5 bg-slate-800 hover:bg-slate-705 text-slate-300 rounded text-xs transition duration-150 flex items-center gap-1 border border-slate-700/40"
            title="Revert modifications to original"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Descartar</span>
          </button>
          <button
            onClick={handleApply}
            disabled={busy}
            className="p-1 px-3 bg-purple-600 hover:bg-purple-750 active:scale-95 text-white rounded text-xs font-semibold transition duration-150 flex items-center gap-1 shadow-md"
            title="Apply modifications back to SWF tag stream"
          >
            {isApplied ? (
              <Check className="w-3.5 h-3.5" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            <span>
              {busy ? "Guardando…" : isApplied ? "Aplicado" : "Aplicar bytes"}
            </span>
          </button>
        </div>
      </div>

      {/* Hex Grid scrolling content */}
      <div className="flex-1 overflow-y-auto p-4 bg-slate-950 font-mono text-xs flex flex-col select-none border-b border-slate-850">
        <div className="space-y-1">
          {/* Header row offset index markings */}
          <div className="flex gap-4 border-b border-slate-900 pb-1.5 text-slate-600 text-[10px] font-semibold">
            <div className="w-16">ADDRESS</div>
            <div className="flex-1 grid grid-cols-16 gap-1 text-center font-bold">
              {Array.from({ length: 16 }).map((_, i) => (
                <div key={i}>{i.toString(16).toUpperCase()}</div>
              ))}
            </div>
            <div className="w-24 text-center">ASCII DECODED</div>
          </div>

          {/* Grid rows */}
          {rows.map((row) => (
            <div
              key={row.offset}
              className="flex gap-4 items-center hover:bg-slate-900/40 py-0.5 px-1 rounded transition duration-150"
            >
              {/* Address label */}
              <div className="w-16 text-slate-500 text-[10px] select-none">
                {row.offset.toString(16).padStart(8, "0").toUpperCase()}:
              </div>

              {/* Bytes grids */}
              <div className="flex-1 grid grid-cols-16 gap-1 text-center">
                {row.bytes.map((byte, idx) => {
                  const absIndex = row.offset + idx;
                  const isEditing = editingIndex === absIndex;
                  return (
                    <div
                      key={idx}
                      role={isEditing ? undefined : "button"}
                      tabIndex={isEditing ? -1 : 0}
                      aria-label={`Editar byte ${absIndex}`}
                      onKeyDown={(event) => {
                        if (
                          !isEditing &&
                          (event.key === "Enter" || event.key === " ")
                        ) {
                          event.preventDefault();
                          handleCellClick(absIndex, byte);
                        }
                      }}
                      onClick={() => handleCellClick(absIndex, byte)}
                      className={`cursor-pointer rounded transition duration-100 ${
                        isEditing
                          ? "bg-slate-800 text-purple-400 font-bold border border-purple-500/50"
                          : bytes[absIndex] !== byte
                            ? "bg-purple-950 text-purple-300 font-bold hover:bg-purple-900"
                            : "hover:bg-slate-800 hover:text-slate-100 text-slate-400"
                      }`}
                    >
                      {isEditing ? (
                        <input
                          aria-label={`Valor hexadecimal del byte ${absIndex}`}
                          type="text"
                          maxLength={2}
                          value={editingValue}
                          onChange={(e) =>
                            setEditingValue(e.target.value.toUpperCase())
                          }
                          onBlur={() => handleHexSubmit(absIndex)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleHexSubmit(absIndex);
                            if (e.key === "Escape") {
                              e.stopPropagation();
                              setEditingIndex(null);
                            }
                          }}
                          className="w-full bg-slate-950 text-purple-400 border-none outline-none text-center rounded font-semibold focus:ring-0 p-0"
                          autoFocus
                        />
                      ) : (
                        byte.toString(16).padStart(2, "0").toUpperCase()
                      )}
                    </div>
                  );
                })}
                {/* Pad columns if the last row is shorter than 16 bytes */}
                {row.bytes.length < 16 &&
                  Array.from({ length: 16 - row.bytes.length }).map((_, i) => (
                    <div key={i} className="text-slate-800 select-none">
                      --
                    </div>
                  ))}
              </div>

              {/* ASCII mapping representation */}
              <div className="w-24 text-slate-500 text-[11px] flex justify-between select-none">
                <span className="text-slate-650 opacity-50 font-bold">|</span>
                <span className="text-emerald-500/80 font-semibold">
                  {row.ascii.join("")}
                </span>
                <span className="text-slate-650 opacity-50 font-bold">|</span>
              </div>
            </div>
          ))}

          {localBytes.length === 0 && (
            <div className="text-slate-600 text-center py-12">
              Empty payload data.
            </div>
          )}
        </div>
      </div>

      <div className="hex-pagination">
        <span>
          {localBytes.length ? page * pageSize + 1 : 0}–
          {Math.min(localBytes.length, (page + 1) * pageSize)} de{" "}
          {localBytes.length} bytes
        </span>
        <div>
          <button
            disabled={!page}
            onClick={() => {
              setPage((value) => value - 1);
              setEditingIndex(null);
            }}
          >
            Página anterior
          </button>
          <button
            disabled={(page + 1) * pageSize >= localBytes.length}
            onClick={() => {
              setPage((value) => value + 1);
              setEditingIndex(null);
            }}
          >
            Página siguiente
          </button>
        </div>
      </div>
      {/* Hex Help Section */}
      <div className="p-3 bg-slate-900 flex items-start gap-2 text-[11px] text-slate-400 leading-relaxed">
        <Info className="w-4 h-4 text-purple-400 flex-shrink-0 mt-0.5 select-none" />
        <p>
          Selecciona un byte para editarlo. Los cambios aparecen en violeta
          hasta aplicar. Después puedes deshacerlos desde el historial y
          exportar el SWF.
        </p>
      </div>
    </div>
  );
}
