import { type FormEvent, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { useMailCounts } from "../api/queries";
import { useCurrentAccount } from "../hooks/useAccount";
import { mailHref, mailView } from "../lib/mail";
import type { SectionId } from "../lib/sections";
import { Icon } from "./Icon";
import { SearchOptions } from "./mail/SearchOptions";
import { SyncIndicator } from "./SyncIndicator";
import { IconButton } from "./ui";

/** Spec §9: the strip at the top of the content: search for the section, and the sync status. */
export function ContentBar({ section, onViews }: { section: SectionId; onViews: () => void }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const mail = section === "mail";
  const [q, setQ] = useState(params.get("q") ?? "");
  // A new section starts from its own search, so a mail query never sits in "Search files".
  const [shownFor, setShownFor] = useState(section);
  if (shownFor !== section) {
    setShownFor(section);
    setQ(params.get("q") ?? "");
  }
  const { pathname } = useLocation();
  const { accountId } = useCurrentAccount();
  const [options, setOptions] = useState(false);
  const onList = pathname === "/mail";
  const labels = useMailCounts(accountId, options).data?.labels.map((l) => l.name) ?? [];
  /** Mail panel spec §5: the search runs in the view and label on screen. An empty search
   * clears it; one letter finds too much (the hint says 2). */
  const searchMail = (text: string) => {
    if (text.length === 1) return;
    const here = pathname === "/mail";
    navigate(mailHref({ view: here ? (params.get("view") ?? undefined) : undefined, label: here ? (params.get("label") ?? undefined) : undefined, q: text || undefined }));
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = q.trim();
    if (mail) return searchMail(text);
    if (text.length < 2) return;
    navigate(`/search?q=${encodeURIComponent(text)}`);
  };
  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-line bg-surface px-3 py-3 sm:px-6">
      <IconButton icon="menu" label="Show views" onClick={onViews} className="md:hidden" />
      <div className="relative min-w-0 max-w-2xl flex-1">
        <form role="search" onSubmit={submit}>
          <div className="flex h-10 items-center gap-2 rounded-menu bg-sunken pl-3.5 pr-1 focus-within:bg-surface focus-within:ring-2 focus-within:ring-accent">
            <Icon name="search" className="text-[20px] text-ink-2" />
            <input
              aria-label={mail ? "Search mail" : "Search files"}
              aria-describedby="search-hint"
              placeholder={mail ? "Search mail: from:, larger:5M, older_than:1y" : "Search in this account"}
              className="min-w-0 flex-1 bg-transparent text-body outline-none placeholder:text-ink-2"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {mail && onList && <IconButton icon="tune" label="Search options" aria-expanded={options} onClick={() => setOptions(!options)} />}
          </div>
          <span id="search-hint" className="sr-only">
            Type at least 2 characters, then press Enter.
          </span>
        </form>
        {options && mail && onList && (
          <SearchOptions
            text={q}
            view={mailView(params.get("view"))}
            labels={labels}
            onClose={() => setOptions(false)}
            onSearch={(text) => {
              setQ(text);
              setOptions(false);
              searchMail(text);
            }}
          />
        )}
      </div>
      <div className="ml-auto">
        <SyncIndicator />
      </div>
    </header>
  );
}
