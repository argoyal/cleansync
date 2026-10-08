import { type FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Brand } from "../pages/LoginPage";
import { AccountSwitcher } from "./AccountSwitcher";
import { Icon } from "./Icon";
import { SyncIndicator } from "./SyncIndicator";
import { IconButton } from "./ui";

export function TopBar({ onMenu }: { onMenu: () => void }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (q.trim().length >= 2) navigate(`/search?q=${encodeURIComponent(q.trim())}`);
  };
  return (
    <header className="flex h-16 shrink-0 items-center gap-2 px-3 sm:gap-4 sm:px-4">
      <IconButton icon="menu" label="Open navigation" onClick={onMenu} className="lg:hidden" />
      <Link to="/" className="shrink-0 rounded-ctl pr-2 max-sm:hidden lg:w-[232px]">
        <Brand />
      </Link>
      <form role="search" onSubmit={submit} className="min-w-0 max-w-2xl flex-1">
        <label className="flex h-11 items-center gap-3 rounded-full bg-sunken px-4 focus-within:bg-surface focus-within:ring-2 focus-within:ring-accent">
          <Icon name="search" className="text-[20px] text-ink-2" />
          <input
            aria-label="Search files"
            placeholder="Search in this account"
            className="min-w-0 flex-1 bg-transparent text-body outline-none placeholder:text-ink-2"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
      </form>
      <div className="ml-auto flex items-center gap-1 sm:gap-2">
        <SyncIndicator />
        <Link to="/settings" aria-label="Settings" title="Settings" className="grid h-10 w-10 place-items-center rounded-full text-ink-2 hover:bg-sunken">
          <Icon name="settings" className="text-[22px]" />
        </Link>
        <AccountSwitcher />
      </div>
    </header>
  );
}
