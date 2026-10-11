import { useEffect } from "react";
import { Link } from "react-router-dom";

import { useHealth } from "../api/queries";
import { DEFAULT_APP_NAME } from "../config";
import type { Badges } from "../hooks/useRailBadges";
import { BADGE_TEXT, type Section, SECTION_STYLE, type SectionId, SECTIONS } from "../lib/sections";
import { Icon } from "./Icon";
import { Mark } from "./Mark";

export function RailBadge({ section, n }: { section: SectionId; n?: number }) {
  if (!n) return null;
  return (
    <span className="absolute right-2 top-1 min-w-[18px] rounded-full bg-rail-badge px-1 text-center text-[11px] font-bold leading-[18px] text-rail-mark tabular">
      <span aria-hidden="true">{n}</span>
      <span className="sr-only">{BADGE_TEXT[section]?.(n)}</span>
    </span>
  );
}

function RailItem({ s, active, badge }: { s: Section; active: boolean; badge?: number }) {
  return (
    <Link
      to={s.to}
      aria-current={active ? "page" : undefined}
      className={`relative flex w-[68px] focus-visible:outline-rail-mark  flex-col items-center gap-1 rounded-card py-2.5 text-meta ${active ? `${SECTION_STYLE[s.id].tile} font-bold` : "hover:bg-rail-ink/10"}`}
    >
      <Icon name={s.icon} className="text-[22px]" />
      {s.label}
      <RailBadge section={s.id} n={badge} />
    </Link>
  );
}

/** Spec §9: the deep indigo icon rail, from tablet width up. */
export function Rail({ section, badges }: { section: SectionId; badges: Badges }) {
  const name = useHealth().data?.app_name ?? DEFAULT_APP_NAME;
  // The rail is on every app page, so it keeps the tab title in step with APP_NAME.
  useEffect(() => {
    document.title = name;
  }, [name]);
  const settings = SECTIONS[SECTIONS.length - 1];
  return (
    <nav aria-label="Sections" className="hidden w-[84px] shrink-0 flex-col items-center gap-2 bg-rail py-4 text-rail-ink md:flex">
      <Link to="/" aria-label={`${name} home`} className="mb-3 rounded-[11px] focus-visible:outline-rail-mark">
        <Mark />
      </Link>
      {SECTIONS.slice(0, -1).map((s) => (
        <RailItem key={s.id} s={s} active={s.id === section} badge={badges[s.id]} />
      ))}
      <div className="mt-auto">
        <RailItem s={settings} active={section === "settings"} />
      </div>
    </nav>
  );
}
