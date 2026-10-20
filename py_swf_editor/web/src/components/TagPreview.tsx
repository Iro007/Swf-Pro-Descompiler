import { Suspense, lazy } from "react";
import { FileCode2 } from "lucide-react";
import { Engine } from "../api";
import { TagInfo } from "../types";

const ShapeViewer = lazy(() => import("./ShapeViewer"));
const ImageViewer = lazy(() => import("./ImageViewer"));
const ScriptViewer = lazy(() => import("./ScriptViewer"));
const SoundViewer = lazy(() => import("./SoundViewer"));
const FontViewer = lazy(() => import("./FontViewer"));
const TextViewer = lazy(() => import("./TextViewer"));
const SpriteViewer = lazy(() => import("./SpriteViewer"));
const ButtonViewer = lazy(() => import("./ButtonViewer"));
const HexPanel = lazy(() => import("./HexPanel"));

interface Props {
  sid: string;
  tag: TagInfo;
  hex: boolean;
  engine: Engine;
  onChanged: () => void;
  onError: (message: string) => void;
  onHex: () => void;
}

export default function TagPreview({
  sid,
  tag,
  hex,
  engine,
  onChanged,
  onError,
  onHex,
}: Props) {
  const props = { sid, tagIndex: tag.index, tagName: tag.name, onError };
  let viewer;
  if (hex) viewer = <HexPanel {...props} onChanged={onChanged} />;
  else if ([2, 22, 32, 83, 46, 84].includes(tag.code))
    viewer = (
      <ShapeViewer
        {...props}
        charId={tag.char_id}
        isMorph={[46, 84].includes(tag.code)}
      />
    );
  else if ([6, 20, 21, 35, 36, 90].includes(tag.code))
    viewer = (
      <ImageViewer {...props} charId={tag.char_id} onChanged={onChanged} />
    );
  else if ([12, 59, 72, 82].includes(tag.code))
    viewer = (
      <ScriptViewer {...props} onChanged={onChanged} initialEngine={engine} />
    );
  else if (tag.code === 39) viewer = <SpriteViewer {...props} />;
  else if ([7, 34].includes(tag.code)) viewer = <ButtonViewer {...props} />;
  else if (tag.code === 14)
    viewer = (
      <SoundViewer {...props} charId={tag.char_id} onChanged={onChanged} />
    );
  else if ([48, 75].includes(tag.code)) viewer = <FontViewer {...props} />;
  else if ([11, 33, 37].includes(tag.code))
    viewer = <TextViewer {...props} tagCode={tag.code} onChanged={onChanged} />;
  else
    viewer = (
      <div className="unsupported-preview">
        <FileCode2 size={36} />
        <h3>{tag.name}</h3>
        <p>
          Este tag no tiene un visor visual. Puedes inspeccionar y editar sus
          bytes en hexadecimal.
        </p>
        <button className="studio-button secondary" onClick={onHex}>
          Inspeccionar bytes
        </button>
      </div>
    );
  return (
    <Suspense
      fallback={
        <div className="viewer-loading" role="status">
          Preparando visor…
        </div>
      }
    >
      {viewer}
    </Suspense>
  );
}
