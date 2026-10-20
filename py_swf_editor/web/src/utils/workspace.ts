import { TagInfo } from "../types";

export const GROUPS = [
  { id: "shapes", label: "Formas y vectores", codes: [2, 22, 32, 83, 46, 84] },
  { id: "images", label: "Imágenes", codes: [6, 20, 21, 35, 36, 90] },
  { id: "scripts", label: "ActionScript", codes: [12, 59, 72, 82] },
  { id: "sprites", label: "Sprites", codes: [39] },
  { id: "sounds", label: "Audio", codes: [14, 15, 18, 19, 45, 89] },
  {
    id: "texts",
    label: "Texto y fuentes",
    codes: [10, 11, 13, 33, 37, 48, 62, 73, 74, 75, 88, 91],
  },
  { id: "buttons", label: "Botones", codes: [7, 17, 23, 34] },
  { id: "frames", label: "Timeline", codes: [1, 4, 5, 26, 28, 43, 70, 86, 94] },
  { id: "video", label: "Vídeo", codes: [60, 61] },
  { id: "other", label: "Datos y metadatos", codes: [] },
];

export function groupFor(code: number) {
  return (
    GROUPS.find((group) => group.codes.includes(code)) ??
    GROUPS[GROUPS.length - 1]
  );
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function matchesTag(tag: TagInfo, query: string) {
  return `${tag.name} ${tag.symbol_name ?? ""} ${tag.char_id ?? ""} ${tag.index}`
    .toLowerCase()
    .includes(query.toLowerCase());
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
