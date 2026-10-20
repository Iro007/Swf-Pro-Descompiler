import React, { useEffect, useState } from "react";
import { Type } from "lucide-react";
import { fontUrl, getFontInfo } from "../api";
import { FontInfo } from "../types";

interface Props {
  sid: string;
  tagIndex: number;
  tagName: string;
  onError: (msg: string) => void;
}

export default function FontViewer({ sid, tagIndex, tagName, onError }: Props) {
  const [info, setInfo] = useState<FontInfo | null>(null);

  useEffect(() => {
    setInfo(null);
    getFontInfo(sid, tagIndex)
      .then(setInfo)
      .catch((e) => onError(`Font info failed: ${e.message}`));
  }, [sid, tagIndex]);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
      <div className="bg-slate-950/80 px-4 py-3 border-b border-slate-800 flex items-center gap-2">
        <Type className="w-4 h-4 text-cyan-400" />
        <span className="font-sans text-sm text-slate-200 font-semibold">{tagName}</span>
        {info && (
          <span className="text-[11px] font-mono text-slate-400">
            “{info.name}” — {info.num_glyphs} glyphs
            {info.bold ? " · bold" : ""}
            {info.italic ? " · italic" : ""}
          </span>
        )}
      </div>
      <div className="p-6 bg-slate-950/40 overflow-auto max-h-[480px]">
        <img src={fontUrl(sid, tagIndex)} alt={tagName} className="max-w-full" />
      </div>
    </div>
  );
}
