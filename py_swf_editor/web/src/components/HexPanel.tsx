import React, { useEffect, useState } from "react";
import { getRawBytes, putRawBytes } from "../api";
import HexEditor from "./HexEditor";

interface Props {
  sid: string;
  tagIndex: number;
  tagName: string;
  onChanged: () => void;
  onError: (msg: string) => void;
}

export default function HexPanel({
  sid,
  tagIndex,
  tagName,
  onChanged,
  onError,
}: Props) {
  const [bytes, setBytes] = useState<Uint8Array | null>(null);

  useEffect(() => {
    let cancelled = false;
    setBytes(null);
    getRawBytes(sid, tagIndex)
      .then((b) => {
        if (!cancelled) setBytes(b);
      })
      .catch((e) => onError(`Could not load tag bytes: ${e.message}`));
    return () => {
      cancelled = true;
    };
  }, [sid, tagIndex]);

  if (!bytes) {
    return (
      <div className="text-xs font-mono text-slate-600 p-8 text-center">
        Loading bytes…
      </div>
    );
  }

  return (
    <HexEditor
      tagIndex={tagIndex}
      tagName={tagName}
      bytes={bytes}
      onUpdateHex={async (idx, updated) => {
        try {
          await putRawBytes(sid, idx, updated);
          setBytes(updated);
          onChanged();
        } catch (error) {
          onError(
            `No se pudieron guardar los bytes: ${(error as Error).message}`,
          );
          throw error;
        }
      }}
    />
  );
}
