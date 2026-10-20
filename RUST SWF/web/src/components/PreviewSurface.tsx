import { useState } from "react";
import { Contrast, Maximize2, ZoomIn, ZoomOut } from "lucide-react";

interface Props {
  src: string;
  alt: string;
  onError?: () => void;
}

export default function PreviewSurface({ src, alt, onError }: Props) {
  const [zoom, setZoom] = useState(1);
  const [background, setBackground] = useState("checker");
  return (
    <div className="preview-surface">
      <div className="preview-tools">
        <span>PREVISUALIZACIÓN</span>
        <div>
          <button
            className="icon-button"
            aria-label="Reducir zoom"
            disabled={zoom <= 0.25}
            onClick={() => setZoom((current) => Math.max(0.25, current - 0.25))}
          >
            <ZoomOut size={14} />
          </button>
          <output aria-label="Nivel de zoom">{Math.round(zoom * 100)}%</output>
          <button
            className="icon-button"
            aria-label="Ampliar zoom"
            disabled={zoom >= 3}
            onClick={() => setZoom((current) => Math.min(3, current + 0.25))}
          >
            <ZoomIn size={14} />
          </button>
          <button
            className="icon-button"
            aria-label="Restablecer zoom"
            title="Restablecer zoom"
            onClick={() => setZoom(1)}
          >
            <Maximize2 size={14} />
          </button>
          <button
            className="icon-button"
            aria-label="Cambiar fondo de vista previa"
            title="Transparencia / claro / oscuro"
            onClick={() =>
              setBackground((current) =>
                current === "checker"
                  ? "light"
                  : current === "light"
                    ? "dark"
                    : "checker",
              )
            }
          >
            <Contrast size={14} />
          </button>
        </div>
      </div>
      <div
        className={`preview-viewport background-${background}`}
        style={{ justifyContent: zoom <= 1 ? "center" : "flex-start" }}
      >
        <div
          className="preview-image-wrap"
          style={{ width: `${zoom * 100}%`, minWidth: `${zoom * 100}%` }}
        >
          <img
            src={src}
            alt={alt}
            onError={onError}
            style={{
              width: "100%",
              height: "auto",
              maxWidth: "none",
              maxHeight: 420 * zoom,
            }}
          />
        </div>
      </div>
    </div>
  );
}
