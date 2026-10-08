import { useEffect, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { useLiveEvents } from "../hooks/live";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";

export function AppShell({ newButton }: { newButton?: React.ReactNode }) {
  useLiveEvents();
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const drawer = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => setNavOpen(false), [location.pathname]);
  // Focus moves into the drawer when it opens and back to the menu button when it closes.
  useEffect(() => {
    if (!navOpen) return;
    opener.current = document.activeElement as HTMLElement | null;
    drawer.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setNavOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener.current?.focus();
    };
  }, [navOpen]);
  return (
    <div className="flex h-full flex-col">
      <TopBar onMenu={() => setNavOpen(true)} />
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-[256px] shrink-0 pl-3 pt-1 lg:block">
          {/* While the drawer is open it holds the one + New button, so there is never a second one. */}
          <SideNav newButton={navOpen ? undefined : newButton} />
        </aside>
        {navOpen && (
          <div className="fixed inset-0 z-40 bg-ink/30 lg:hidden" onClick={() => setNavOpen(false)}>
            <aside
              ref={drawer}
              role="dialog"
              aria-modal="true"
              aria-label="Navigation"
              tabIndex={-1}
              className="h-full w-[280px] bg-bg pl-3 pt-4 outline-none"
              onClick={(e) => {
                e.stopPropagation();
                // Any link closes the drawer, even one to the page already open.
                if ((e.target as HTMLElement).closest("a")) setNavOpen(false);
              }}
            >
              <SideNav newButton={newButton} />
            </aside>
          </div>
        )}
        <main className="mb-3 mr-0 min-w-0 flex-1 overflow-y-auto rounded-none bg-surface sm:mr-3 sm:rounded-card">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
