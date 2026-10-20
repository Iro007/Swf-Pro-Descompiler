import React, { useEffect, useState } from "react";
import { Check, FileText, Save } from "lucide-react";
import { getEditTextInfo, getTextSvg, updateTextString } from "../api";

interface Props {
  sid: string;
  tagIndex: number;
  tagName: string;
  tagCode: number;
  onChanged?: () => void;
  onError: (msg: string) => void;
}

export default function TextViewer({ sid, tagIndex, tagName, tagCode, onChanged, onError }: Props) {
  const [svg, setSvg] = useState<string | null>(null);
  const [props, setProps] = useState<Record<string, unknown> | null>(null);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setSvg(null);
    setProps(null);
    setEditText("");
    if (tagCode === 37) {
      getEditTextInfo(sid, tagIndex)
        .then((p) => {
          setProps(p);
          setEditText(typeof p.initial_text === "string" ? p.initial_text : "");
        })
        .catch((e) => onError(`EditText parse failed: ${e.message}`));
    } else {
      getTextSvg(sid, tagIndex)
        .then(setSvg)
        .catch((e) => onError(`Text render failed: ${e.message}`));
    }
  }, [sid, tagIndex, tagCode]);

  const handleSaveText = async () => {
    setSaving(true);
    try {
      await updateTextString(sid, tagIndex, editText);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      onChanged?.();
    } catch (e: any) {
      onError(`Text save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
      <div className="bg-slate-950/80 px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-violet-400" />
          <span className="font-sans text-sm text-slate-200 font-semibold">{tagName}</span>
        </div>
        {tagCode === 37 && (
          <button
            onClick={handleSaveText}
            disabled={saving}
            className="p-1 px-3 bg-violet-600 hover:bg-violet-700 active:scale-95 text-white rounded text-xs font-semibold transition flex items-center gap-1 shadow disabled:opacity-50"
            title="Save text to SWF"
          >
            {saved ? <Check className="w-3.5 h-3.5" /> : <Save className="w-3.5 h-3.5" />}
            <span>{saved ? "Saved!" : "Save Text"}</span>
          </button>
        )}
      </div>

      {svg && (
        <div className="p-6 bg-white/95 flex items-center justify-center min-h-[160px]">
          <div className="[&>svg]:max-w-full" dangerouslySetInnerHTML={{ __html: svg }} />
        </div>
      )}

      {props && tagCode === 37 && (
        <div className="p-5 space-y-4">
          <div>
            <label className="block text-xs font-mono text-slate-400 mb-1.5 font-semibold">
              Edit Initial Text (String):
            </label>
            <textarea
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              rows={4}
              className="w-full bg-slate-950 border border-slate-800 text-slate-100 font-mono text-xs p-3 rounded-lg focus:outline-none focus:border-violet-500 leading-relaxed resize-y"
              placeholder="Initial text value..."
            />
          </div>

          <div>
            <span className="block text-xs font-mono text-slate-400 mb-1.5 font-semibold">Properties:</span>
            <div className="bg-slate-950 border border-slate-800 p-4 rounded-lg font-mono text-xs text-slate-200 divide-y divide-slate-900 max-h-[260px] overflow-y-auto">
              {Object.entries(props).map(([key, val]) => (
                <div key={key} className="flex justify-between py-1.5 gap-6">
                  <span className="text-slate-500">{key}:</span>
                  <span className="text-violet-300 truncate max-w-md text-right">
                    {typeof val === "object" ? JSON.stringify(val) : String(val)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {!svg && !props && (
        <div className="p-8 text-center text-xs font-mono text-slate-600">Loading…</div>
      )}
    </div>
  );
}

