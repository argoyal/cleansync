import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { useStatus } from "../api/queries";
import { useCurrentAccount } from "../hooks/useAccount";
import { Icon, type IconName } from "./Icon";
import { StorageBars } from "./StorageBars";

function Item({ to, icon, label, badge, end }: { to: string; icon: IconName; label: string; badge?: number; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `flex h-9 items-center gap-3.5 rounded-full pl-4 pr-3 text-body ${
          isActive ? "bg-accent-soft font-semibold text-ink" : "text-ink hover:bg-sunken"
        }`
      }
    >
      <Icon name={icon} className="text-[20px] text-ink-2" />
      <span className="flex-1 truncate">{label}</span>
      {!!badge && (
        <span className="rounded-full bg-warn px-2 text-meta font-semibold text-white tabular" aria-label={`${badge} open`}>
          {badge}
        </span>
      )}
    </NavLink>
  );
}

/** Features of a later version: shown so the layout is complete, but not clickable yet. */
function Later({ icon, label }: { icon: IconName; label: string }) {
  return (
    <span
      aria-disabled="true"
      title="Not available yet"
      className="flex h-9 cursor-default items-center gap-3.5 rounded-full pl-4 pr-3 text-ink-2/60"
    >
      <Icon name={icon} className="text-[20px]" />
      {label}
    </span>
  );
}

export function SideNav({ newButton }: { newButton?: ReactNode }) {
  const { data: status } = useStatus();
  const { accountId } = useCurrentAccount();
  return (
    <nav aria-label="Main" className="flex h-full flex-col gap-0.5 overflow-y-auto pb-4 pr-3">
      {newButton && <div className="mb-3 pl-1">{newButton}</div>}
      <Item to="/" end icon="home" label="Home" />
      <Item to="/drive" icon="folder" label="My Drive" />
      <Later icon="inventory_2" label="Offloaded" />
      <Item to="/recent" icon="schedule" label="Recent" />
      <Item to="/starred" icon="star" label="Starred" />
      <div className="my-2 border-t border-line" />
      <Item to="/conflicts" icon="compare_arrows" label="Conflicts" badge={status?.conflicts} />
      <Item to="/activity" icon="sync" label="Sync activity" />
      <Later icon="history" label="Recovery" />
      <div className="my-2 border-t border-line" />
      <Item to="/storage" icon="storage" label="Storage" />
      <div className="mt-2">
        <StorageBars accountId={accountId} compact />
      </div>
    </nav>
  );
}
