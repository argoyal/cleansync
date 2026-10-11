import {
  type ButtonHTMLAttributes,
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
  useEffect, useLayoutEffect, useId,
  useRef,
  useState,
} from "react";

import { useDismiss } from "../hooks/useDismiss";
import { Icon, type IconName } from "./Icon";

type Tone = "primary" | "plain" | "danger" | "quiet" | "main";

const TONES: Record<Tone, string> = {
  primary: "bg-accent text-accent-ink hover:bg-accent/90",
  plain: "border border-line bg-surface text-ink hover:bg-sunken",
  danger: "bg-err text-white hover:bg-err/90",
  quiet: "text-ink-2 hover:bg-sunken hover:text-ink",
  main: "bg-rail font-semibold text-white hover:bg-rail/90",
};

export function Button({
  tone = "plain",
  icon,
  children,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: Tone; icon?: IconName }) {
  return (
    <button
      type="button"
      className={`inline-flex h-9 items-center gap-2 rounded-ctl px-3.5 text-body font-medium disabled:cursor-not-allowed disabled:opacity-50 ${TONES[tone]} ${className}`}
      {...rest}
    >
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}

export function IconButton({
  icon,
  label,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: IconName; label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`grid h-9 w-9 place-items-center rounded-full text-ink-2 hover:bg-sunken hover:text-ink disabled:opacity-40 ${className}`}
      {...rest}
    >
      <Icon name={icon} className="text-[20px]" />
    </button>
  );
}

export function Dialog({
  title,
  onClose,
  children,
  footer,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  // Once, when it opens: focus the first field or marked button. A new onClose on a re-render
  // (a poll, a typed letter) must not pull focus back. When it closes, focus goes back to
  // what had it before, if that is still on the page.
  useEffect(() => {
    const back = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = box.current?.querySelector<HTMLElement>("input, select, textarea, button[data-autofocus]");
    first?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close.current();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (back?.isConnected && back !== document.body) back.focus();
    };
  }, []);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4" onMouseDown={onClose}>
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`w-full ${wide ? "max-w-2xl" : "max-w-md"} rounded-card bg-surface p-6 shadow-float`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-title font-semibold">{title}</h2>
        <div className="space-y-3 text-body">{children}</div>
        {footer && <div className="mt-6 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

export type MenuItem =
  | { label: string; icon?: IconName; onSelect: () => void; disabled?: boolean; danger?: boolean }
  | { separator: true }
  | { note: string }
  | { label: string; icon?: IconName; submenu: MenuItem[] };

/** A floating menu at a fixed point (right-click) or under an anchor; closes on outside click. */
export function Menu({
  items,
  x,
  y,
  onClose,
  label,
  opener = null,
}: {
  opener?: HTMLElement | null;
  items: MenuItem[];
  x: number;
  y: number;
  onClose: () => void;
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x, y });
  const [open, setOpen] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos({
      x: Math.max(8, Math.min(x, window.innerWidth - r.width - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - r.height - 8)),
    });
  }, [x, y]);
  // Focus goes back to what opened the menu (the ⋮ button or the tile), captured each time it opens.
  const returnTo = useRef<HTMLElement | null>(opener ?? (document.activeElement as HTMLElement | null));
  useDismiss(ref, onClose, true, returnTo as React.RefObject<HTMLElement>);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("[role=menuitem]:not([disabled])")?.focus();
  }, []);
  const onKeyDown = (e: React.KeyboardEvent) => {
    const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
    if (!keys.includes(e.key)) return;
    const els = Array.from(ref.current?.querySelectorAll<HTMLElement>(":scope > [role=menuitem]:not([disabled])") ?? []);
    if (els.length === 0) return;
    e.preventDefault();
    const at = els.indexOf(document.activeElement as HTMLElement);
    let next = 0;
    if (e.key === "ArrowDown") next = (at + 1) % els.length;
    else if (e.key === "ArrowUp") next = (at - 1 + els.length) % els.length;
    else if (e.key === "End") next = els.length - 1;
    els[next].focus();
  };
  return (
    <div
      ref={ref}
      role="menu"
      aria-label={label}
      onKeyDown={onKeyDown}
      style={{ left: pos.x, top: pos.y }}
      className="fixed z-50 min-w-[220px] rounded-menu border border-line bg-surface py-1.5 shadow-float"
    >
      {items.map((item, i) =>
        "note" in item ? (
          <p key={i} className="max-w-[280px] px-4 py-2 text-meta text-ink-2">
            {item.note}
          </p>
        ) : "separator" in item ? (
          <div key={i} role="separator" className="my-1.5 border-t border-line" />
        ) : "submenu" in item ? (
          <div key={i} className="relative" onMouseEnter={() => setOpen(i)} onMouseLeave={() => setOpen(null)}>
            <button
              type="button"
              role="menuitem"
              aria-haspopup="menu"
              aria-expanded={open === i}
              className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-sunken focus:bg-sunken"
              onClick={() => setOpen(i)}
            >
              {item.icon && <Icon name={item.icon} className="text-ink-2" />}
              <span className="flex-1">{item.label}</span>
              <Icon name="chevron_right" className="text-ink-2" />
            </button>
            {open === i && (
              <div
                role="menu"
                aria-label={item.label}
                className="absolute left-full top-0 z-50 min-w-[230px] rounded-menu border border-line bg-surface py-1.5 shadow-float max-sm:left-4 max-sm:top-full"
              >
                {item.submenu.map((sub, j) =>
                  "onSelect" in sub ? (
                    <MenuButton key={j} item={sub} onClose={onClose} />
                  ) : null,
                )}
              </div>
            )}
          </div>
        ) : (
          <MenuButton key={i} item={item} onClose={onClose} />
        ),
      )}
    </div>
  );
}

function MenuButton({
  item,
  onClose,
}: {
  item: { label: string; icon?: IconName; onSelect: () => void; disabled?: boolean; danger?: boolean };
  onClose: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={item.disabled}
      className={`flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-sunken focus:bg-sunken disabled:opacity-40 ${item.danger ? "text-err" : ""}`}
      onClick={() => {
        onClose();
        item.onSelect();
      }}
    >
      {item.icon && <Icon name={item.icon} className={item.danger ? "" : "text-ink-2"} />}
      {item.label}
    </button>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  const hintId = useId();
  const control =
    hint && isValidElement(children)
      ? cloneElement(children as ReactElement<{ "aria-describedby"?: string }>, { "aria-describedby": hintId })
      : children;
  return (
    <div>
      <label className="block">
        <span className="mb-1 block text-table font-medium text-ink-2">{label}</span>
        {control}
      </label>
      {hint && (
        <span id={hintId} className="mt-1 block text-meta text-ink-2">
          {hint}
        </span>
      )}
    </div>
  );
}

export const inputClass =
  "h-10 w-full rounded-ctl border border-line bg-surface px-3 text-body text-ink placeholder:text-ink-2/70 focus:border-accent focus:outline-none";

export function Bar({ value, tone = "accent", label }: { value: number; tone?: "accent" | "run" | "warn" | "err"; label: string }) {
  const pct = Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0;
  const color = { accent: "bg-accent", run: "bg-run", warn: "bg-warn", err: "bg-err" }[tone];
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className="h-1.5 w-full overflow-hidden rounded-full bg-sunken"
    >
      <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Notice({ tone, children }: { tone: "warn" | "err" | "run" | "ok"; children: ReactNode }) {
  const styles = {
    warn: "border-warn/40 bg-warn-soft text-ink",
    err: "border-err/40 bg-err-soft text-ink",
    run: "border-run/30 bg-run-soft text-ink",
    ok: "border-ok/30 bg-surface text-ink",
  }[tone];
  const icon: IconName = { warn: "warning", err: "error", run: "sync", ok: "check_circle" }[tone] as IconName;
  const iconColor = { warn: "text-warn", err: "text-err", run: "text-run", ok: "text-ok" }[tone];
  return (
    <div role={tone === "err" ? "alert" : "status"} className={`flex gap-3 rounded-card border px-4 py-3 ${styles}`}>
      <Icon name={icon} className={`mt-0.5 text-[18px] ${iconColor}`} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: IconName; title: string; children?: ReactNode }) {
  return (
    <div className="mx-auto flex max-w-sm flex-col items-center py-16 text-center">
      <Icon name={icon} className="mb-3 text-[40px] text-ink-2/60" />
      <p className="text-section font-semibold">{title}</p>
      {children && <div className="mt-2 text-ink-2">{children}</div>}
    </div>
  );
}
