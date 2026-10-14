import { useEffect, useRef, useState } from "react";

import { type Sender, STATUS_TEXT, type UnsubscribeJobItem } from "../../api/unsubscribe";
import { formatBytes, formatWhen } from "../../lib/format";
import { Icon } from "../Icon";
import { Button, Menu, type MenuItem } from "../ui";
import { ItemActions } from "./UnsubscribeJobDialog";

export type PickState = "none" | "some" | "all";
export type RowAction = "unsubscribe" | "auto_archive" | "keep" | "open" | "offload" | "forever";

function PickAllBox({ state, onChange }: { state: PickState; onChange: () => void }) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (box.current) box.current.indeterminate = state === "some";
  }, [state]);
  return <input ref={box} type="checkbox" className="h-4 w-4 accent-accent" aria-label="Pick all" checked={state === "all"} onChange={onChange} />;
}

function RowMenu({ sender, forever, onAction }: { sender: Sender; forever: boolean; onAction: (a: RowAction) => void }) {
  const [at, setAt] = useState<{ x: number; y: number; opener: HTMLElement } | null>(null);
  const items: MenuItem[] = [
    { label: "Auto-archive", onSelect: () => onAction("auto_archive") },
    { label: "Keep", onSelect: () => onAction("keep") },
    { label: "Open their mail", onSelect: () => onAction("open") },
    { label: "Offload their mail", onSelect: () => onAction("offload") },
    ...(forever ? [{ label: "Delete forever their mail", danger: true, onSelect: () => onAction("forever") }] : []),
  ];
  return (
    <>
      <button
        type="button"
        aria-label={`More actions for ${sender.address}`}
        aria-haspopup="menu"
        className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-ink-2 hover:bg-sunken"
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setAt({ x: r.left, y: r.bottom + 4, opener: e.currentTarget });
        }}
      >
        <Icon name="more_vert" />
      </button>
      {at && <Menu label={`Actions for ${sender.address}`} items={items} x={at.x} y={at.y} opener={at.opener} onClose={() => setAt(null)} />}
    </>
  );
}

/** The sender list (0.5.2 spec §3.2): who, how much, how much is read and in the Inbox, the
 * last mail, the status, and what to do. `latest` is each sender's item in the latest job. */
export function SenderTable({
  accountId,
  senders,
  latest,
  forever,
  pickState,
  isPicked,
  onToggle,
  onPickAll,
  onAction,
  busy = false,
}: {
  accountId: number;
  senders: Sender[];
  latest: Map<string, UnsubscribeJobItem>;
  forever: boolean;
  pickState: PickState;
  isPicked: (address: string) => boolean;
  onToggle: (address: string) => void;
  onPickAll: () => void;
  onAction: (action: RowAction, sender: Sender) => void;
  busy?: boolean;
}) {
  return (
    <div className="overflow-x-auto rounded-card border border-line">
      <table className="w-full min-w-[760px] text-left text-table">
        <thead className="border-b border-line text-meta font-semibold text-ink-2">
          <tr>
            <th scope="col" className="w-10 px-3 py-2">
              <PickAllBox state={pickState} onChange={onPickAll} />
            </th>
            <th scope="col" className="px-3 py-2">Sender</th>
            <th scope="col" className="px-3 py-2 text-right">Mails</th>
            <th scope="col" className="px-3 py-2 text-right">Read</th>
            <th scope="col" className="px-3 py-2 text-right">In Inbox</th>
            <th scope="col" className="px-3 py-2">Last mail</th>
            <th scope="col" className="px-3 py-2 text-right">Size</th>
            <th scope="col" className="px-3 py-2">Status</th>
            <th scope="col" className="px-3 py-2">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {senders.map((s) => {
            const item = latest.get(s.address);
            return (
              <tr key={s.address} className="border-b border-line last:border-b-0 align-top">
                <td className="px-3 py-2.5">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-accent"
                    aria-label={`Pick ${s.address}`}
                    checked={isPicked(s.address)}
                    onChange={() => onToggle(s.address)}
                  />
                </td>
                <td className="max-w-[260px] px-3 py-2.5">
                  <span className="block truncate font-medium">{s.name || s.address}</span>
                  <span className="block truncate text-meta text-ink-2">{s.address}</span>
                </td>
                <td className="px-3 py-2.5 text-right tabular">{s.count.toLocaleString()}</td>
                <td className="px-3 py-2.5 text-right tabular">{s.read_pct}%</td>
                <td className="px-3 py-2.5 text-right tabular">{s.inbox_pct}%</td>
                <td className="whitespace-nowrap px-3 py-2.5 tabular">{formatWhen(s.last_at)}</td>
                <td className="whitespace-nowrap px-3 py-2.5 text-right tabular">{formatBytes(s.bytes)}</td>
                <td className="max-w-[240px] px-3 py-2.5">
                  {s.status && <span className="block font-medium">{STATUS_TEXT[s.status]}</span>}
                  {item?.note && <span className="block text-meta text-ink-2">{item.note}</span>}
                  {item && <ItemActions accountId={accountId} item={item} />}
                </td>
                <td className="px-3 py-2.5">
                  <span className="flex items-center justify-end gap-1">
                    <Button aria-label={`Unsubscribe from ${s.address}`} disabled={busy} onClick={() => onAction("unsubscribe", s)}>
                      Unsubscribe
                    </Button>
                    <RowMenu sender={s} forever={forever} onAction={(a) => onAction(a, s)} />
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
