import React, { useEffect, useState } from "react";
import { Download, Shapes } from "lucide-react";
import { getSvg } from "../api";
import PreviewSurface from "./PreviewSurface";

interface Props {
  sid: string;
  tagIndex: number;
  tagName: string;
  charId: number | null;
  isMorph?: boolean;
  onError: (msg: string) => void;
}

export default function ShapeViewer({
  sid,
  tagIndex,
  tagName,
  charId,
  isMorph,
  onError,
}: Props) {
  const [svg, setSvg] = useState<string | null>(null);
  const [ratio, setRatio] = useState(0);

  useEffect(() => {
    setRatio(0);
  }, [sid, tagIndex]);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    getSvg(sid, tagIndex, ratio)
      .then((text) => {
        if (!cancelled) setSvg(text);
      })
      .catch((e) => onError(`Shape render failed: ${e.message}`));
    return () => {
      cancelled = true;
    };
  }, [sid, tagIndex, ratio]);

  const downloadSvg = () => {
    if (!svg) return;
    const blob = new Blob([svg], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `shape_${charId ?? tagIndex}.svg`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
      <div className="bg-slate-950/80 px-4 py-3 border-b border-slate-800 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <Shapes className="w-4 h-4 text-emerald-400" />
          <span className="font-sans text-sm text-slate-200 font-semibold">
            {tagName}
          </span>
          {charId != null && (
            <span className="text-[10px] font-mono text-slate-500">
              char id #{charId}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {isMorph && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-slate-500">
                morph
              </span>
              <input
                aria-label="Interpolación del morph"
                type="range"
                min={0}
                max={100}
                value={Math.round(ratio * 100)}
                onChange={(e) => setRatio(parseInt(e.target.value, 10) / 100)}
                className="w-32 accent-emerald-500"
              />
              <span className="text-[10px] font-mono text-slate-400 w-8">
                {Math.round(ratio * 100)}%
              </span>
            </div>
          )}
          <button
            onClick={downloadSvg}
            disabled={!svg}
            className="p-1 px-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs transition flex items-center gap-1 border border-slate-700/40 disabled:opacity-40"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Export SVG</span>
          </button>
        </div>
      </div>
      <div className="min-h-[280px] bg-slate-950/40">
        {svg ? (
          <PreviewSurface
            src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`}
            alt={`${tagName} · character ${charId ?? tagIndex}`}
            onError={() => onError("No se pudo mostrar la forma")}
          />
        ) : (
          <span className="text-xs font-mono text-slate-600">
            Rendering shape…
          </span>
        )}
      </div>
    </div>
  );
}
