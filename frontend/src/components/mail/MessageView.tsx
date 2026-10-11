import type { ReactNode } from "react";

import { mailAttachmentUrl, mailEmlUrl } from "../../api/queries";
import type { MailMessageInfo } from "../../api/types";
import { formatBytes, formatWhen } from "../../lib/format";
import { labelDot } from "../../lib/mail";
import { Icon } from "../Icon";
import { Notice } from "../ui";
import { MailFrame } from "./MailFrame";

const STATE_MARK: Record<string, string> = { offloaded: "Offloaded", deleted_in_provider: "Deleted in Gmail" };

/** What can be done with one mail (spec §6.2). Plan 4 adds Restore to Gmail and Offload. */
export function MessageActions({ message, extra }: { message: MailMessageInfo; extra?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-table">
      {!message.file_missing && (
        <a className="font-semibold text-accent" href={mailEmlUrl(message.id)}>
          Download .eml
        </a>
      )}
      {message.gmail_url && (
        <a className="font-semibold text-accent" href={message.gmail_url} target="_blank" rel="noopener noreferrer">
          Open in Gmail
        </a>
      )}
      {extra}
    </div>
  );
}

function Attachments({ message }: { message: MailMessageInfo }) {
  if (message.attachments.length === 0) return null;
  return (
    <ul aria-label="Attachments" className="flex flex-wrap gap-2">
      {message.attachments.map((a) => (
        <li key={a.index} className="flex items-center gap-2 rounded-ctl border border-line px-3 py-2 text-table">
          <Icon name="attach_file" className="text-ink-2" />
          <span className="max-w-[220px] truncate">{a.name}</span>
          <span className="text-meta text-ink-2 tabular">{formatBytes(a.size)}</span>
          {a.preview && (
            <a className="font-semibold text-accent" href={mailAttachmentUrl(message.id, a.index)} target="_blank" rel="noopener noreferrer" aria-label={`Preview ${a.name}`}>
              Preview
            </a>
          )}
          <a className="font-semibold text-accent" href={mailAttachmentUrl(message.id, a.index, true)} aria-label={`Download ${a.name}`}>
            Download
          </a>
        </li>
      ))}
    </ul>
  );
}

export function MessageView({ message, open, onToggle, actions }: { message: MailMessageInfo; open: boolean; onToggle: () => void; actions?: ReactNode }) {
  const mark = STATE_MARK[message.state];
  return (
    <article className="rounded-card border border-line bg-surface">
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left">
        <span className="sr-only">{`${message.subject || "(no subject)"}, from ${message.from_addr}, ${formatWhen(message.date)}`}</span>
        <span aria-hidden="true" className="min-w-0 flex-1 truncate font-semibold">
          {message.from_addr}
        </span>
        {mark && <span className="rounded-full bg-sec-mail-soft px-2 text-meta text-sec-mail-soft-ink">{mark}</span>}
        <time aria-hidden="true" className="text-meta text-ink-2 tabular" dateTime={message.date}>
          {formatWhen(message.date)}
        </time>
      </button>
      {open && (
        <div className="space-y-3 border-t border-line px-4 py-3">
          <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-0.5 text-table">
            <dt className="text-ink-2">To</dt>
            <dd className="break-words">{message.to_addrs || "—"}</dd>
            {message.cc_addrs && (
              <>
                <dt className="text-ink-2">Cc</dt>
                <dd className="break-words">{message.cc_addrs}</dd>
              </>
            )}
          </dl>
          {message.labels.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {message.labels.map((l) => (
                <span key={l.id} className="inline-flex items-center gap-1.5 rounded-full bg-sunken px-2 text-meta">
                  <span aria-hidden="true" className={`h-2 w-2 rounded-full ${labelDot(l.id)}`} />
                  {l.name}
                </span>
              ))}
            </div>
          )}
          <MessageActions message={message} extra={actions} />
          {message.file_missing ? <Notice tone="err">This mail's file is missing on the storage disk.</Notice> : <MailFrame message={message} />}
          {!message.file_missing && <Attachments message={message} />}
        </div>
      )}
    </article>
  );
}
