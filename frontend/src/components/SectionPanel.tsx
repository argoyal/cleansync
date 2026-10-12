import { type ReactNode, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";

import { friendlyError } from "../api/client";
import { useMailCounts, useStatus } from "../api/queries";
import { useCurrentAccount } from "../hooks/useAccount";
import { useSetUpMail } from "../hooks/useSetUpMail";
import { labelDot, MAIL_VIEWS, mailHref } from "../lib/mail";
import { linkActive, SECTION_STYLE, type SectionId, SECTIONS } from "../lib/sections";
import { TABS } from "../pages/SettingsPage";
import { AccountSwitcher } from "./AccountSwitcher";
import { Icon, type IconName } from "./Icon";
import { StorageBars } from "./StorageBars";
import { useToast } from "./Toasts";
import { Button } from "./ui";

export function PanelLink({
  to,
  label,
  icon,
  count,
  badge,
  active,
  section,
}: {
  to: string;
  label: string;
  icon?: IconName;
  count?: number;
  badge?: ReactNode;
  active?: boolean;
  section: SectionId;
}) {
  const { pathname, search } = useLocation();
  const on = active ?? linkActive(to, pathname, search);
  return (
    <Link
      to={to}
      aria-current={on ? "page" : undefined}
      className={`flex items-center gap-2.5 rounded-ctl px-2.5 py-2 text-body ${on ? `${SECTION_STYLE[section].soft} font-semibold` : "text-ink hover:bg-sunken"}`}
    >
      {icon && <Icon name={icon} className="text-[18px] opacity-80" />}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {count !== undefined && (
        <>
          {/* The space keeps "Inbox 1,902" apart for screen readers; flex hides it on screen. */}{" "}
          <span className="text-meta tabular text-ink-2">{count.toLocaleString()}</span>
        </>
      )}
      {badge}
    </Link>
  );
}

export function PanelHeading({ children }: { children: ReactNode }) {
  return <div className="mt-3 px-2.5 py-1 text-meta font-bold uppercase tracking-[0.06em] text-ink-2">{children}</div>;
}

/** Shown in a panel instead of its views when the section isn't set up for this account. */
export function SetUpCard({ title, text, action }: { title: string; text: string; action: ReactNode }) {
  return (
    <section aria-label={title} className="mt-1 rounded-card border border-line bg-surface p-3">
      <h3 className="mb-1 text-body font-semibold">{title}</h3>
      <p className="mb-3 text-table text-ink-2">{text}</p>
      {action}
    </section>
  );
}

function FilesViews() {
  const { accountId } = useCurrentAccount();
  const conflicts = useStatus().data?.conflicts ?? 0;
  return (
    <>
      <PanelLink section="files" to="/drive" label="My Drive" icon="folder" />
      <PanelLink section="files" to="/recent" label="Recent" icon="schedule" />
      <PanelLink section="files" to="/starred" label="Starred" icon="star" />
      <PanelLink section="files" to="/offloaded" label="Offloaded" icon="inventory_2" />
      <PanelLink
        section="files"
        to="/conflicts"
        label="Conflicts"
        icon="compare_arrows"
        badge={
          conflicts ? (
            <span className="rounded-full bg-warn px-2 text-meta font-semibold text-accent-ink tabular">
              <span aria-hidden="true">{conflicts}</span>
              <span className="sr-only">{conflicts} open {conflicts === 1 ? "conflict" : "conflicts"}</span>
            </span>
          ) : undefined
        }
      />
      <Link
        to={`/setup?step=folders${accountId !== null ? `&account=${accountId}` : ""}`}
        className="mt-2 flex items-center gap-2.5 rounded-ctl px-2.5 py-2 text-body text-ink-2 hover:bg-sunken hover:text-ink"
      >
        <Icon name="cloud_sync" className="text-[18px]" />
        Sync a Drive folder…
      </Link>
      <div className="mt-auto pt-4">
        <StorageBars accountId={accountId} compact />
      </div>
    </>
  );
}

const LABELS_OPEN_KEY = "drivesync.mailLabelsOpen";

function labelsOpen(): boolean {
  try {
    return localStorage.getItem(LABELS_OPEN_KEY) !== "0";
  } catch {
    return true; // storage blocked: start unfolded
  }
}

/** The Mail views (spec §6.1, §9) with their counts, then the user labels. */
export function MailViews() {
  const { account } = useCurrentAccount();
  const counts = useMailCounts(account?.id ?? null).data;
  // "/mail/rules" sits under "/mail", which would light up All mail too.
  const onRules = useLocation().pathname === "/mail/rules";
  const [open, setOpenState] = useState(labelsOpen);
  const setOpen = (next: boolean) => {
    setOpenState(next);
    try {
      localStorage.setItem(LABELS_OPEN_KEY, next ? "1" : "0");
    } catch {
      // storage blocked: the fold lasts for this visit only
    }
  };
  return (
    <>
      {MAIL_VIEWS.map((v) => (
        <PanelLink
          key={v.id}
          section="mail"
          to={mailHref({ view: v.id })}
          label={v.label}
          icon={v.icon}
          count={counts?.views[v.id]}
          active={onRules ? false : undefined}
        />
      ))}
      <PanelLink section="mail" to="/mail/rules" label="Rules" icon="view_list" />
      {counts && counts.labels.length > 0 && (
        <>
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="mt-3 flex items-center gap-1 px-2.5 py-1 text-left text-meta font-bold uppercase tracking-[0.06em] text-ink-2 hover:text-ink"
          >
            Labels
            <Icon name="keyboard_arrow_down" className={`text-[16px] transition-transform ${open ? "" : "-rotate-90"}`} />
          </button>
          {open && (
            <ul aria-label="Labels" className="flex flex-col gap-0.5">
              {counts.labels.map((l) => (
                <li key={l.id}>
                  <PanelLink
                    section="mail"
                    to={mailHref({ label: l.id })}
                    label={l.name}
                    count={l.count}
                    badge={<span aria-hidden="true" className={`order-first h-2 w-2 shrink-0 rounded-full ${labelDot(l.id)}`} />}
                  />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  );
}

function ActivityViews() {
  return (
    <>
      <PanelLink section="activity" to="/activity" label="Sync activity" icon="sync" />
      <PanelLink section="activity" to="/storage" label="Storage" icon="storage" />
    </>
  );
}

function RecoveryViews() {
  return (
    <>
      <PanelLink section="recovery" to="/recovery" label="All" />
      <PanelLink section="recovery" to="/recovery?source=files" label="Files" />
      <PanelLink section="recovery" to="/recovery?source=mail" label="Mail" />
    </>
  );
}

/** The Settings tabs, from the Settings page's own list (the page keeps its strip on phones). */
export const SETTINGS_TABS = Object.entries(TABS) as [keyof typeof TABS, string][];

function SettingsViews() {
  const [params] = useSearchParams();
  const asked = params.get("tab") ?? "";
  // The same fallback as the page: an unknown tab shows General.
  const tab = Object.hasOwn(TABS, asked) ? asked : "general";
  return (
    <>
      {SETTINGS_TABS.map(([id, label]) => (
        <PanelLink key={id} section="settings" to={`/settings?tab=${id}`} label={label} active={tab === id} />
      ))}
    </>
  );
}

function SetUpMail() {
  const { account } = useCurrentAccount();
  const setUp = useSetUpMail();
  const toast = useToast();
  if (!account) return null;
  if (account.services.mail === "setting_up") {
    return (
      <SetUpCard
        title="Finish setting up Mail"
        text="Gmail permission is in place. Start the first backup."
        action={
          <Link to="/mail" className="text-table font-semibold text-accent">
            Start mail backup
          </Link>
        }
      />
    );
  }
  return (
    <SetUpCard
      title="Set up Mail"
      text="Keep a copy of every Gmail message on this server."
      action={
        <Button
          tone="main"
          disabled={setUp.isPending}
          onClick={() => setUp.mutate(account.id, { onError: (e) => toast(friendlyError(e), { tone: "error" }) })}
        >
          Set up mail backup
        </Button>
      }
    />
  );
}

export function SectionPanel({ section, newButton }: { section: SectionId; newButton?: ReactNode }) {
  const { account } = useCurrentAccount();
  const label = SECTIONS.find((s) => s.id === section)?.label ?? "";
  const filesReady = !account || account.services.files === "active";
  const mailReady = !!account && !["not_set_up", "setting_up"].includes(account.services.mail);
  return (
    <div className="flex h-full min-h-0 flex-col px-3 py-4">
      <AccountSwitcher />
      {section === "files" && newButton && <div className="mt-3">{newButton}</div>}
      <h2 className="px-2.5 pb-2 pt-4 text-title font-bold">{label}</h2>
      {section === "files" && !filesReady && (
        <SetUpCard
          title="Set up Files"
          text="Files from this account aren't synced here yet."
          action={
            <Link to="/setup" className="text-table font-semibold text-accent">
              Set up Files
            </Link>
          }
        />
      )}
      {section === "mail" && account && !mailReady && <SetUpMail />}
      {((section === "files" && filesReady) || (section === "mail" && mailReady) || !["files", "mail"].includes(section)) && (
        <nav aria-label={`${label} views`} className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
          {section === "files" && <FilesViews />}
          {section === "mail" && <MailViews />}
          {section === "activity" && <ActivityViews />}
          {section === "recovery" && <RecoveryViews />}
          {section === "settings" && <SettingsViews />}
        </nav>
      )}
    </div>
  );
}
