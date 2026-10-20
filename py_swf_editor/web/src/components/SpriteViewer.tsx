import React, { useEffect, useState } from "react";
import { Film } from "lucide-react";
import { getSpriteInfo, spriteFrameSvgUrl } from "../api";

interface Props {
  sid: string;
  tagIndex: number;
  tagName: string;
  onError: (msg: string) => void;
}

export default function SpriteViewer({ sid, tagIndex, tagName, onError }: Props) {
  const [frameCount, setFrameCount] = useState(0);
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    setFrame(0);
    setFrameCount(0);
    getSpriteInfo(sid, tagIndex)
      .then((info) => setFrameCount(info.frame_count))
      .catch((e) => onError(`Sprite info failed: ${e.message}`));
  }, [sid, tagIndex]);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
      <div className="bg-slate-950/80 px-4 py-3 border-b border-slate-800 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <Film className="w-4 h-4 text-orange-400" />
          <span className="font-sans text-sm text-slate-200 font-semibold">{tagName}</span>
          <span className="text-[9px] uppercase font-mono px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">
            aproximado
          </span>
        </div>
        {frameCount > 1 && (
          <div className="flex items-center gap-3 flex-1 max-w-xs">
            <input
              type="range"
              min={0}
              max={frameCount - 1}
              value={frame}
              onChange={(e) => setFrame(parseInt(e.target.value, 10))}
              className="flex-1 accent-orange-500"
            />
            <span className="text-[10px] font-mono text-slate-400 w-14 text-right">
              {frame + 1} / {frameCount}
            </span>
          </div>
        )}
      </div>
      <div className="p-6 bg-slate-950/40 flex items-center justify-center min-h-[240px]">
        {frameCount > 0 ? (
          <img
            src={spriteFrameSvgUrl(sid, tagIndex, frame)}
            alt={`${tagName} frame ${frame + 1}`}
            className="max-w-full max-h-[420px]"
          />
        ) : (
          <span className="text-xs font-mono text-slate-600">Sprite vacío (sin frames renderizables)</span>
        )}
      </div>
    </div>
  );
}
