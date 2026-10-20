import React, { useRef, useState } from "react";
import { Download, Image as ImageIcon, RefreshCw } from "lucide-react";
import { imageUrl, replaceResource } from "../api";

interface Props {
  sid: string;
  tagIndex: number;
  tagName: string;
  charId: number | null;
  onChanged: () => void;
  onError: (msg: string) => void;
}

export default function ImageViewer({ sid, tagIndex, tagName, charId, onChanged, onError }: Props) {
  const [cacheBust, setCacheBust] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const src = `${imageUrl(sid, tagIndex)}?v=${cacheBust}`;

  const handleReplace = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      await replaceResource(sid, tagIndex, file);
      setCacheBust((v) => v + 1);
      onChanged();
    } catch (e: any) {
      onError(`Replace failed: ${e.message}`);
    } finally {
      event.target.value = "";
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
      <div className="bg-slate-950/80 px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ImageIcon className="w-4 h-4 text-blue-400" />
          <span className="font-sans text-sm text-slate-200 font-semibold">{tagName}</span>
          {charId != null && (
            <span className="text-[10px] font-mono text-slate-500">char id #{charId}</span>
          )}
        </div>
        <div className="flex gap-2">
          <a
            href={src}
            download={`char_${charId ?? tagIndex}`}
            className="p-1 px-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs transition flex items-center gap-1 border border-slate-700/40"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Export</span>
          </a>
          <button
            onClick={() => fileInput.current?.click()}
            className="p-1 px-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded text-xs font-semibold transition flex items-center gap-1"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Replace</span>
          </button>
          <input ref={fileInput} type="file" accept="image/*" onChange={handleReplace} className="hidden" />
        </div>
      </div>
      <div
        className="p-6 flex items-center justify-center min-h-[280px]"
        style={{
          backgroundImage:
            "linear-gradient(45deg, #1e293b 25%, transparent 25%), linear-gradient(-45deg, #1e293b 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #1e293b 75%), linear-gradient(-45deg, transparent 75%, #1e293b 75%)",
          backgroundSize: "16px 16px",
          backgroundPosition: "0 0, 0 8px, 8px -8px, -8px 0px",
        }}
      >
        <img
          src={src}
          alt={tagName}
          className="max-w-full max-h-[420px] shadow-lg"
          onError={() => onError("Image could not be decoded by the backend")}
        />
      </div>
    </div>
  );
}
