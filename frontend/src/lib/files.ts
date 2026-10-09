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

/** Spec §8: nothing new can be added where it would never reach Google Drive. */
export const NOT_SYNCED_NOTE = "This folder isn't synced with Google Drive. Open a synced folder to add files.";

/** The same idea for the Move dialog: a move out of Drive's sync would leave the file only here. */
/** The server's own refusal text (store.IN_OFFLOADED_MSG). */
export const IN_OFFLOADED_NOTE =
  "This folder is offloaded: it's kept only on this server and doesn't sync with Google Drive. Restore it to Drive to add files here.";

export const NOT_SYNCED_MOVE_NOTE = "This folder isn't synced with Google Drive. Open a synced folder to move items.";

/** Shown in folders of a Drive-to-here pair: the next sync undoes local changes there. */
export const ONE_WAY_NOTE =
  "This folder only receives changes from Google Drive. Add, rename, move or delete files on Google Drive; the next sync brings the change here.";

/** The server refuses to rename or move an item on Drive while it has changes waiting to sync. */
export const isWaitingToSync = (message: string) => /changes waiting to sync/i.test(message);
