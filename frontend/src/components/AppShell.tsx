import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { useLiveEvents } from "../hooks/live";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";

export function AppShell({ newButton }: { newButton?: React.ReactNode }) {
  useLiveEvents();
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setNavOpen(false), [location.pathname]);
  return (
    <div className="flex h-full flex-col">
      <TopBar onMenu={() => setNavOpen(true)} />
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-[256px] shrink-0 pl-3 pt-1 lg:block">
          <SideNav newButton={newButton} />
        </aside>
        {navOpen && (
          <div className="fixed inset-0 z-40 bg-ink/30 lg:hidden" onClick={() => setNavOpen(false)}>
            <aside className="h-full w-[280px] bg-bg pl-3 pt-4" onClick={(e) => e.stopPropagation()}>
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
