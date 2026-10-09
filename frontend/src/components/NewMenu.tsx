import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api, friendlyError } from "../api/client";
import { useBrowse } from "../api/queries";
import { NOT_SYNCED_NOTE } from "../lib/files";
import { uploads } from "../hooks/uploads";
import { useCurrentAccount } from "../hooks/useAccount";
import { useInvalidateFiles } from "./FileActions";
import { Icon } from "./Icon";
import { useToast } from "./Toasts";
import { Button, Dialog, Field, inputClass, Menu, type MenuItem, Notice } from "./ui";

type Kind = "docx" | "xlsx" | "pptx" | "document" | "spreadsheet" | "presentation";

/** The folder the person is looking at: the + New menu adds things there. */
function useCurrentFolder(): string {
  const [params] = useSearchParams();
  return params.get("path") ?? "";
}

export function NewMenu() {
  const { accountId } = useCurrentAccount();
  const folder = useCurrentFolder();
  const toast = useToast();
  const invalidate = useInvalidateFiles();
  const button = useRef<HTMLButtonElement>(null);
  const files = useRef<HTMLInputElement>(null);
  const dirs = useRef<HTMLInputElement>(null);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [newFolder, setNewFolder] = useState(false);
  // A Drive-to-here folder takes no local additions; Google files are made on Drive itself.
  const browse = useBrowse(accountId, folder).data;
  const oneWay = browse?.pair?.direction === "REMOTE_TO_LOCAL";
  // Outside every sync pair nothing new would ever reach Drive (spec §8).
  const notSynced = browse !== undefined && !browse.pair;
  const off = oneWay || notSynced;

  const create = useMutation({
    mutationFn: ({ kind }: { kind: Kind; tab: Window | null }) =>
      api<{ path: string | null; google_url: string | null }>("/api/files/new", {
        method: "POST",
        body: { account_id: accountId, path: folder, kind },
      }),
    onSuccess: (out, { tab, kind }) => {
      invalidate();
      const url = out.google_url;
      if (url && tab) {
        tab.location.href = url;
      } else if (url) {
        // The browser blocked the tab opened during the click: offer a link instead.
        const what = { document: "Google Doc", spreadsheet: "Google Sheet", presentation: "Google Slides file" }[kind as string] ?? "Google file";
        toast(`Your new ${what} is ready.`, {
          action: { label: "Open it", run: () => window.open(url, "_blank", "noopener") },
        });
      } else {
        tab?.close();
        toast(`Created ${out.path?.split("/").pop()}. It reaches Google Drive with the next sync.`);
      }
    },
    onError: (e, { tab }) => {
      tab?.close();
      toast(friendlyError(e), { tone: "error" });
    },
  });

  // Google files open in a new tab. The tab must be opened during the click itself, or the
  // browser blocks it as a pop-up; it is pointed at the document once Drive answers.
  const newFile = (kind: Kind) => {
    const google = kind === "document" || kind === "spreadsheet" || kind === "presentation";
    const tab = google ? window.open("", "_blank") : null;
    if (tab) tab.opener = null;
    create.mutate({ kind, tab });
  };

  const items: MenuItem[] = [
    ...(notSynced ? [{ note: NOT_SYNCED_NOTE }, { separator: true } as const] : []),
    { label: "New folder", icon: "create_new_folder", onSelect: () => setNewFolder(true), disabled: off },
    { separator: true },
    { label: "Upload files", icon: "upload_file", onSelect: () => files.current?.click(), disabled: off },
    { label: "Upload folder", icon: "drive_folder_upload", onSelect: () => dirs.current?.click(), disabled: off },
    { separator: true },
    {
      label: "Google Docs",
      icon: "article",
      submenu: [
        { label: "Blank Google Doc", onSelect: () => newFile("document"), disabled: notSynced },
        { label: "Word file (.docx)", onSelect: () => newFile("docx"), disabled: off },
      ],
    },
    {
      label: "Google Sheets",
      icon: "table_chart",
      submenu: [
        { label: "Blank Google Sheet", onSelect: () => newFile("spreadsheet"), disabled: notSynced },
        { label: "Excel file (.xlsx)", onSelect: () => newFile("xlsx"), disabled: off },
      ],
    },
    {
      label: "Google Slides",
      icon: "slideshow",
      submenu: [
        { label: "Blank Google Slides", onSelect: () => newFile("presentation"), disabled: notSynced },
        { label: "PowerPoint file (.pptx)", onSelect: () => newFile("pptx"), disabled: off },
      ],
    },
  ];

  const picked = (list: FileList | null) => {
    if (accountId === null || !list) return;
    if (list.length === 0) {
      toast("That folder is empty, so there is nothing to upload.");
      return;
    }
    uploads.add(Array.from(list), accountId, folder);
  };

  return (
    <>
      <button
        ref={button}
        type="button"
        disabled={accountId === null}
        className="flex h-14 items-center gap-3 rounded-card bg-surface pl-4 pr-6 text-body font-semibold shadow-float hover:bg-accent-soft disabled:opacity-50"
        onClick={() => {
          const r = button.current?.getBoundingClientRect();
          setMenu({ x: r?.left ?? 16, y: (r?.top ?? 16) + 4 });
        }}
      >
        <Icon name="add" className="text-[24px] text-accent" />
        New
      </button>
      <input ref={files} type="file" multiple hidden aria-label="Files to upload" onChange={(e) => (picked(e.target.files), (e.target.value = ""))} />
      <input
        ref={dirs}
        type="file"
        multiple
        hidden
        aria-label="Folder to upload"
        {...{ webkitdirectory: "" }}
        onChange={(e) => (picked(e.target.files), (e.target.value = ""))}
      />
      {menu && <Menu label="New" opener={button.current} items={items} x={menu.x} y={menu.y} onClose={() => setMenu(null)} />}
      {newFolder && accountId !== null && (
        <NewFolderDialog accountId={accountId} folder={folder} onClose={() => setNewFolder(false)} />
      )}
    </>
  );
}

function NewFolderDialog({ accountId, folder, onClose }: { accountId: number; folder: string; onClose: () => void }) {
  const [name, setName] = useState("Untitled folder");
  const invalidate = useInvalidateFiles();
  const make = useMutation({
    mutationFn: () => api("/api/files/mkdir", { method: "POST", body: { account_id: accountId, path: folder, name: name.trim() } }),
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    make.mutate();
  };
  return (
    <Dialog
      title="New folder"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button tone="primary" type="submit" form="new-folder" disabled={!name.trim() || make.isPending}>
            Create
          </Button>
        </>
      }
    >
      <form id="new-folder" onSubmit={submit}>
        <Field label="Folder name">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} onFocus={(e) => e.target.select()} />
        </Field>
      </form>
      {make.isError && <Notice tone="err">{friendlyError(make.error)}</Notice>}
    </Dialog>
  );
}
