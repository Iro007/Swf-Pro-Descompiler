import React, { useEffect, useState } from "react";
import { MousePointer2 } from "lucide-react";

interface ButtonRecord {
  character_id: number;
  depth: number;
  states: string[];
  matrix: Record<string, number>;
}

interface ButtonInfo {
  character_id: number;
  flags: number;
  records: ButtonRecord[];
  actions: string;
  parse_error?: string | null;
}

interface Props {
  sid: string;
  tagIndex: number;
  tagName: string;
  onError: (message: string) => void;
}

export default function ButtonViewer({ sid, tagIndex, tagName, onError }: Props) {
  const [info, setInfo] = useState<ButtonInfo | null>(null);
  const [state, setState] = useState("up");

  useEffect(() => {
    fetch(`/api/files/${sid}/tags/${tagIndex}/button-info`)
      .then(async (response) => {
        if (!response.ok) throw new Error((await response.json()).detail ?? response.statusText);
        return response.json() as Promise<ButtonInfo>;
      })
      .then(setInfo)
      .catch((error) => onError(`Button parse failed: ${error.message}`));
  }, [sid, tagIndex, onError]);

  const records = info?.records.filter((record) => record.states.includes(state)) ?? [];
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-xl space-y-5">
      <div className="flex items-center gap-2">
        <MousePointer2 className="w-5 h-5 text-pink-400" />
        <h2 className="text-sm font-semibold text-slate-100">{tagName}</h2>
        <span className="text-[10px] font-mono text-slate-500">button #{info?.character_id ?? "…"}</span>
      </div>
      <div className="flex gap-2">
        {["up", "over", "down", "hit"].map((value) => (
          <button
            key={value}
            onClick={() => setState(value)}
            className={`px-3 py-1.5 rounded text-xs font-mono uppercase ${state === value ? "bg-pink-600 text-white" : "bg-slate-950 text-slate-400"}`}
          >
            {value}
          </button>
        ))}
      </div>
      <div className="bg-slate-950 rounded-lg p-4 space-y-2">
        <div className="text-xs text-slate-400">Objects in state: {records.length}</div>
        {records.map((record, index) => (
          <div key={`${record.character_id}-${index}`} className="text-xs font-mono text-slate-300 flex justify-between border-b border-slate-800 py-2">
            <span>character #{record.character_id}</span>
            <span className="text-slate-500">depth {record.depth}</span>
          </div>
        ))}
      </div>
      {info?.actions && (
        <pre className="bg-slate-950 rounded-lg p-4 text-xs font-mono text-slate-300 overflow-auto max-h-64">{info.actions}</pre>
      )}
      {info?.parse_error && <p className="text-xs text-amber-300">{info.parse_error}</p>}
    </div>
  );
}
