import { type FormEvent, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import type { SectionId } from "../lib/sections";
import { Icon } from "./Icon";
import { SyncIndicator } from "./SyncIndicator";
import { IconButton } from "./ui";

/** Spec §9: the strip at the top of the content: search for the section, and the sync status. */
export function ContentBar({ section, onViews }: { section: SectionId; onViews: () => void }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const mail = section === "mail";
  const [q, setQ] = useState(params.get("q") ?? "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = q.trim();
    if (text.length < 2) return;
    navigate(mail ? `/mail?q=${encodeURIComponent(text)}` : `/search?q=${encodeURIComponent(text)}`);
  };
  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-line bg-surface px-3 py-3 sm:px-6">
      <IconButton icon="menu" label="Show views" onClick={onViews} className="md:hidden" />
      <form role="search" onSubmit={submit} className="min-w-0 max-w-2xl flex-1">
        <label className="flex h-10 items-center gap-2 rounded-menu bg-sunken px-3.5 focus-within:bg-surface focus-within:ring-2 focus-within:ring-accent">
          <Icon name="search" className="text-[20px] text-ink-2" />
          <input
            aria-label={mail ? "Search mail" : "Search files"}
            aria-describedby="search-hint"
            placeholder={mail ? "Search mail: from:, larger:5M, older_than:1y" : "Search in this account"}
            className="min-w-0 flex-1 bg-transparent text-body outline-none placeholder:text-ink-2"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <span id="search-hint" className="sr-only">
          Type at least 2 characters, then press Enter.
        </span>
      </form>
      <div className="ml-auto">
        <SyncIndicator />
      </div>
    </header>
  );
}
