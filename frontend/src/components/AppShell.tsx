import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { useHealth, useStatus } from "../api/queries";
import { DEFAULT_APP_NAME } from "../config";
import { useLiveEvents } from "../hooks/live";
import { useRailBadges } from "../hooks/useRailBadges";
import { sectionFor } from "../lib/sections";
import { BottomBar } from "./BottomBar";
import { ContentBar } from "./ContentBar";
import { Mark } from "./Mark";
import { Rail } from "./Rail";
import { SectionPanel } from "./SectionPanel";
import { keepTabInside, Notice } from "./ui";

/** Spec §9, draft B: icon rail, section panel, content (a bottom bar and a sheet on phones). */
export function AppShell({ newButton }: { newButton?: ReactNode }) {
  useLiveEvents();
  const location = useLocation();
  const section = sectionFor(location.pathname);
  const badges = useRailBadges();
  const stopped = useStatus().data?.sync_stopped;
  const name = useHealth().data?.app_name ?? DEFAULT_APP_NAME;
  const [sheet, setSheet] = useState(false);
  const sheetBox = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => setSheet(false), [location.pathname, location.search]);
  // Focus moves into the sheet when it opens and back to its button when it closes.
  useEffect(() => {
    if (!sheet) return;
    opener.current = document.activeElement as HTMLElement | null;
    sheetBox.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSheet(false);
      if (e.key === "Tab" && sheetBox.current) keepTabInside(e, sheetBox.current);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener.current?.focus();
    };
  }, [sheet]);
  return (
    <div className="flex h-full bg-bg">
      <Rail section={section} badges={badges} />
      <aside aria-label="Section" className="hidden w-[236px] shrink-0 border-r border-line bg-panel md:block">
        {/* While the sheet is open it holds the one New button, so there is never a second one. */}
        <SectionPanel section={section} newButton={sheet ? undefined : newButton} />
      </aside>
      {sheet && (
        <div className="fixed inset-0 z-40 bg-ink/30 md:hidden" onClick={() => setSheet(false)}>
          <aside
            ref={sheetBox}
            role="dialog"
            aria-modal="true"
            aria-label="Views"
            tabIndex={-1}
            className="h-full w-[280px] bg-panel outline-none"
            onClick={(e) => {
              e.stopPropagation();
              // Any link closes the sheet, even one to the page already open.
              if ((e.target as HTMLElement).closest("a")) setSheet(false);
            }}
          >
            {/* On phones the rail is hidden, so the sheet carries the way Home. */}
            <Link to="/" aria-label={`${name} home`} className="ml-3 mt-4 inline-block rounded-[11px]">
              <Mark size={32} onRail={false} />
            </Link>
            <SectionPanel section={section} newButton={newButton} />
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col pb-[calc(4rem+env(safe-area-inset-bottom))] md:pb-0">
        <ContentBar section={section} onViews={() => setSheet(true)} />
        {stopped && (
          <div className="px-3 pt-3 sm:px-6">
            <Notice tone="err">{stopped}</Notice>
          </div>
        )}
        <main className="min-h-0 flex-1 overflow-y-auto p-0 sm:p-4">
          <div className="min-h-full bg-surface sm:rounded-card sm:border sm:border-line">
            <Outlet />
          </div>
        </main>
      </div>
      <BottomBar section={section} badges={badges} />
    </div>
  );
}
