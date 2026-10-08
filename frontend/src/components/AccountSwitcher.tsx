import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { api, clearCsrfToken, friendlyError } from "../api/client";
import { forgetAccount, useCurrentAccount } from "../hooks/useAccount";
import { useDismiss } from "../hooks/useDismiss";
import { useConnectGoogle } from "../hooks/useConnect";
import { Icon } from "./Icon";
import { ACCOUNT_DOT } from "./StatusBadge";
import { useToast } from "./Toasts";

const STATUS_TEXT = { CONNECTED: "Connected", NEEDS_REAUTH: "Needs reconnecting", DISCONNECTED: "Disconnected" };

export function Avatar({ email, size = 36 }: { email: string; size?: number }) {
  return (
    <span
      aria-hidden="true"
      style={{ width: size, height: size }}
      className="grid shrink-0 place-items-center rounded-full bg-accent text-section font-semibold uppercase text-accent-ink"
    >
      {email.slice(0, 1)}
    </span>
  );
}

/** The avatar top right: shows the current Google account and switches between them. */
export function AccountSwitcher() {
  const { account, accounts, setAccountId } = useCurrentAccount();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useDismiss(box, () => setOpen(false), open, trigger);
  const toast = useToast();
  const connect = useConnectGoogle();
  const qc = useQueryClient();
  const signOut = async () => {
    setOpen(false);
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch (e) {
      toast(friendlyError(e), { tone: "error" });
      return;
    }
    clearCsrfToken();
    forgetAccount();
    qc.clear(); // leaving the app also closes the live stream
    navigate("/login", { replace: true });
  };
  return (
    <div className="relative" ref={box}>
      <button
        ref={trigger}
        type="button"
        aria-label={account ? `Account: ${account.email}` : "Accounts"}
        aria-expanded={open}
        className="rounded-full p-0.5 hover:ring-4 hover:ring-sunken"
        onClick={() => setOpen(!open)}
      >
        {account ? <Avatar email={account.email} /> : <Icon name="person_add" className="m-2 text-[20px] text-ink-2" />}
      </button>
      {open && (
        <div
          role="group"
          aria-label="Google accounts"
          className="absolute right-0 top-12 z-50 w-[min(320px,calc(100vw-2rem))] rounded-card border border-line bg-surface py-2 shadow-float"
        >
          {accounts.map((a) => (
            <button
              key={a.id}
              type="button"
              aria-pressed={a.id === account?.id}
              disabled={a.status === "DISCONNECTED"}
              className={`flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-50 ${a.id === account?.id ? "bg-accent-soft" : ""}`}
              onClick={() => {
                setAccountId(a.id);
                setOpen(false);
                trigger.current?.focus();
                // Settings and Storage are not about one folder, so stay there.
                if (!/^\/(settings|storage)(\/|$)/.test(pathname)) navigate("/drive");
              }}
            >
              <Avatar email={a.email} size={30} />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{a.email}</span>
                <span className="flex items-center gap-1.5 text-meta text-ink-2">
                  <span className={`h-2 w-2 rounded-full ${ACCOUNT_DOT[a.status]}`} />
                  {STATUS_TEXT[a.status]}
                </span>
              </span>
            </button>
          ))}
          <div className="my-1.5 border-t border-line" />
          <button
            type="button"
            className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-sunken"
            onClick={() =>
              connect.mutate(undefined, { onError: (e) => toast(friendlyError(e), { tone: "error" }) })
            }
          >
            <Icon name="person_add" className="text-ink-2" /> Add Google account
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-sunken"
            onClick={() => {
              setOpen(false);
              trigger.current?.focus();
              navigate("/settings?tab=accounts");
            }}
          >
            <Icon name="settings" className="text-ink-2" /> Manage accounts
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-sunken"
            onClick={() => void signOut()}
          >
            <Icon name="logout" className="text-ink-2" /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
