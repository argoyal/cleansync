import type { FileItem } from "../api/types";
import type { IconName } from "../components/Icon";

const OFFICE = /\.(docx?|xlsx?|pptx?)$/i;

export const isOffice = (item: FileItem) => !item.is_dir && OFFICE.test(item.name);

/** Can Chrome show it in a tab? Mirrors the server's serve_policy. */
export function opensInBrowser(item: FileItem): boolean {
  const m = item.mime ?? "";
  return (
    m === "application/pdf" ||
    m.startsWith("image/") ||
    m.startsWith("video/") ||
    m.startsWith("audio/") ||
    m.startsWith("text/") ||
    m === "application/json"
  );
}

export function iconFor(item: FileItem): IconName {
  if (item.is_dir) return "folder";
  if (item.native === "spreadsheet" || /\.(xlsx?|csv)$/i.test(item.name)) return "table_chart";
  if (item.native === "presentation" || /\.pptx?$/i.test(item.name)) return "slideshow";
  if (item.native === "document" || /\.docx?$/i.test(item.name)) return "article";
  const m = item.mime ?? "";
  if (m === "application/pdf") return "picture_as_pdf";
  if (m.startsWith("image/")) return "image";
  if (m.startsWith("video/")) return "movie";
  if (m.startsWith("audio/")) return "audio_file";
  return "draft";
}

/** Paths of a conflict's copies are relative to the pair folder; the browser wants account paths. */
export function accountPath(pairSubpath: string, pairRelative: string): string {
  return pairSubpath ? `${pairSubpath}/${pairRelative}` : pairRelative;
}

export function parentOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}
