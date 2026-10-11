import { Link } from "react-router-dom";

import type { Badges } from "../hooks/useRailBadges";
import { SECTION_STYLE, type SectionId, SECTIONS } from "../lib/sections";
import { Icon } from "./Icon";
import { RailBadge } from "./Rail";

/** Spec §9: on phones the rail becomes a bar along the bottom with the same sections. */
export function BottomBar({ section, badges }: { section: SectionId; badges: Badges }) {
  return (
    <nav aria-label="Sections at the bottom" className="fixed inset-x-0 bottom-0 z-30 flex h-16 items-center justify-around bg-rail px-1 text-rail-ink md:hidden">
      {SECTIONS.map((s) => (
        <Link
          key={s.id}
          to={s.to}
          aria-current={s.id === section ? "page" : undefined}
          className={`relative flex min-w-[60px] flex-col items-center gap-0.5 rounded-ctl px-1.5 py-1.5 text-meta ${s.id === section ? `${SECTION_STYLE[s.id].tile} font-bold` : ""}`}
        >
          <Icon name={s.icon} className="text-[20px]" />
          {s.label}
          <RailBadge section={s.id} n={badges[s.id]} />
        </Link>
      ))}
    </nav>
  );
}
