import React, { useEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Gamepad2,
  Maximize2,
  Pause,
  Play,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { downloadUrl, frameSvgUrl, getTimelineInfo } from "../api";
import PreviewSurface from "./PreviewSurface";

interface Props {
  sid: string;
  onError: (msg: string) => void;
}

declare global {
  interface Window {
    RufflePlayer?: {
      newest: () => {
        createPlayer: () => HTMLElement & {
          load: (options: { url: string }) => Promise<void>;
          pause?: () => void;
          play?: () => void;
        };
      };
    };
  }
}

export default function StageViewer({ sid, onError }: Props) {
  const [viewMode, setViewMode] = useState<"svg" | "ruffle">("svg");
  const [frameCount, setFrameCount] = useState(0);
  const [frame, setFrame] = useState(0);
  const [frameRate, setFrameRate] = useState(12);
  const [playing, setPlaying] = useState(false);
  const [ruffleLoading, setRuffleLoading] = useState(false);
  const [ruffleReady, setRuffleReady] = useState(false);
  const ruffleContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setFrame(0);
    setFrameCount(0);
    setPlaying(false);
    getTimelineInfo(sid)
      .then((info) => {
        setFrameCount(info.frame_count);
        setFrameRate(info.frame_rate || 12);
      })
      .catch((e) => onError(`Timeline info failed: ${e.message}`));
  }, [sid]);

  useEffect(() => {
    if (!playing || frameCount < 2 || viewMode !== "svg") return;
    const timer = window.setInterval(
      () => {
        setFrame((current) => (current + 1) % frameCount);
      },
      1000 / Math.max(1, frameRate),
    );
    return () => window.clearInterval(timer);
  }, [playing, frameCount, frameRate, viewMode]);

  // Load Ruffle Wasm player when in Ruffle mode
  useEffect(() => {
    if (viewMode !== "ruffle" || !sid) return;

    let mounted = true;
    setRuffleLoading(true);

    const initRuffle = () => {
      try {
        if (!window.RufflePlayer) {
          throw new Error("Ruffle library not loaded");
        }
        const ruffle = window.RufflePlayer.newest();
        if (!ruffle || !ruffleContainerRef.current) return;

        ruffleContainerRef.current.innerHTML = "";
        const player = ruffle.createPlayer();
        player.style.width = "100%";
        player.style.height = "420px";
        ruffleContainerRef.current.appendChild(player);

        player
          .load({ url: downloadUrl(sid) })
          .then(() => {
            if (mounted) {
              setRuffleLoading(false);
              setRuffleReady(true);
            }
          })
          .catch((err: any) => {
            if (mounted) {
              setRuffleLoading(false);
              onError(`Ruffle player failed to load SWF: ${err.message}`);
            }
          });
      } catch (err: any) {
        if (mounted) {
          setRuffleLoading(false);
          onError(`Ruffle initialization failed: ${err.message}`);
        }
      }
    };

    if (!window.RufflePlayer) {
      const existingScript = document.getElementById("ruffle-script");
      if (!existingScript) {
        const script = document.createElement("script");
        script.id = "ruffle-script";
        script.src = "https://unpkg.com/@ruffle-rs/ruffle";
        script.async = true;
        script.onload = () => {
          if (mounted) initRuffle();
        };
        script.onerror = () => {
          if (mounted) {
            setRuffleLoading(false);
            onError(
              "Could not load Ruffle WebAssembly script. Check internet connection.",
            );
          }
        };
        document.head.appendChild(script);
      } else {
        existingScript.addEventListener("load", initRuffle);
      }
    } else {
      initRuffle();
    }

    return () => {
      mounted = false;
      if (ruffleContainerRef.current) {
        ruffleContainerRef.current.innerHTML = "";
      }
    };
  }, [viewMode, sid]);

  if (frameCount === 0) return null;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-md">
      <div className="px-4 py-3 bg-slate-950/80 border-b border-slate-800 flex justify-between items-center gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <Clapperboard className="w-4 h-4 text-emerald-400" />
          <span className="text-xs font-mono font-bold text-slate-300">
            Escenario
          </span>

          {/* Mode Switcher */}
          <div className="flex bg-slate-950 p-0.5 border border-slate-800 rounded-lg">
            <button
              type="button"
              onClick={() => setViewMode("svg")}
              className={`px-2 py-0.5 text-[11px] font-medium rounded transition flex items-center gap-1 ${
                viewMode === "svg"
                  ? "bg-slate-800 text-emerald-400 shadow-sm"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              Frames SVG
            </button>
            <button
              type="button"
              onClick={() => setViewMode("ruffle")}
              className={`px-2 py-0.5 text-[11px] font-medium rounded transition flex items-center gap-1 ${
                viewMode === "ruffle"
                  ? "bg-emerald-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-slate-200"
              }`}
              title="Interactive playback with Ruffle (Flash WebAssembly emulator)"
            >
              <Gamepad2 className="w-3 h-3" />
              Reproductor Ruffle
            </button>
          </div>
        </div>

        {viewMode === "svg" ? (
          <div className="flex items-center gap-3 flex-1 max-w-sm">
            <button
              type="button"
              title={playing ? "Pause" : "Play"}
              aria-label={playing ? "Pause timeline" : "Play timeline"}
              onClick={() => setPlaying((current) => !current)}
              className="p-1 text-emerald-400 hover:text-emerald-300 disabled:text-slate-600"
              disabled={frameCount < 2}
            >
              {playing ? (
                <Pause className="w-3.5 h-3.5" />
              ) : (
                <Play className="w-3.5 h-3.5" />
              )}
            </button>
            <button
              type="button"
              title="Previous frame"
              aria-label="Previous frame"
              onClick={() =>
                setFrame((current) => (current - 1 + frameCount) % frameCount)
              }
              className="p-1 text-slate-400 hover:text-white disabled:text-slate-600"
              disabled={frameCount < 2}
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <input
              aria-label="Frame del timeline"
              type="range"
              min={0}
              max={Math.max(0, frameCount - 1)}
              value={frame}
              onChange={(e) => setFrame(parseInt(e.target.value, 10))}
              className="flex-1 accent-emerald-500"
            />
            <button
              type="button"
              title="Next frame"
              aria-label="Next frame"
              onClick={() => setFrame((current) => (current + 1) % frameCount)}
              className="p-1 text-slate-400 hover:text-white disabled:text-slate-600"
              disabled={frameCount < 2}
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
            <span className="text-[10px] font-mono text-slate-400 w-16 text-right">
              {frame + 1} / {frameCount}
            </span>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              Reproducción interactiva
            </span>
            <button
              type="button"
              onClick={() => {
                if (ruffleContainerRef.current) {
                  ruffleContainerRef.current.innerHTML = "";
                  const ruffle = window.RufflePlayer?.newest();
                  if (ruffle) {
                    const player = ruffle.createPlayer();
                    player.style.width = "100%";
                    player.style.height = "420px";
                    ruffleContainerRef.current.appendChild(player);
                    player.load({ url: downloadUrl(sid) });
                  }
                }
              }}
              title="Restart Ruffle Player"
              className="p-1 text-slate-400 hover:text-white"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>

      <div className="bg-slate-950/40 min-h-[300px]">
        {viewMode === "svg" ? (
          <PreviewSurface
            src={frameSvgUrl(sid, frame)}
            alt={`frame ${frame + 1}`}
            onError={() => onError("Frame could not be rendered")}
          />
        ) : (
          <div className="w-full relative flex flex-col items-center justify-center">
            {ruffleLoading && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-950/80 z-10 text-slate-300">
                <RefreshCw className="w-6 h-6 animate-spin text-emerald-400" />
                <span className="text-xs font-mono">
                  Iniciando Ruffle Flash Player (WebAssembly)...
                </span>
              </div>
            )}
            <div
              ref={ruffleContainerRef}
              className="w-full min-h-[420px] flex items-center justify-center bg-black/60 rounded-lg overflow-hidden border border-slate-800"
            />
          </div>
        )}
      </div>
    </div>
  );
}
