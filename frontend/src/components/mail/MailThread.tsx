import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { friendlyError } from "../../api/client";
import { useMailThread } from "../../api/queries";
import type { Account } from "../../api/types";
import { mailHref, mailView } from "../../lib/mail";
import { Icon } from "../Icon";
import { Notice } from "../ui";
import { type ForeverOne, MessageMailActions } from "./MailActions";
import { MessageView } from "./MessageView";

/** Wide screens show the list beside the open mail (the xl breakpoint); phones one at a time. */
export const isNarrow = () => !(window.matchMedia?.("(min-width: 1280px)").matches ?? false);

export function MailThread({ account, threadId, onBack, onForever }: { account: Account; threadId: string; onBack?: () => void; onForever: (start: ForeverOne) => void }) {
  const [params] = useSearchParams();
  const thread = useMailThread(account.id, threadId, mailView(params.get("view")));
  const messages = thread.data ?? [];
  const [open, setOpen] = useState<Set<number>>(new Set());
  const newest = messages.at(-1)?.id;
  useEffect(() => setOpen(new Set(newest !== undefined ? [newest] : [])), [threadId, newest]);
  const heading = useRef<HTMLHeadingElement>(null);
  // On a phone the list goes away when a mail opens: take focus to what took its place.
  useEffect(() => {
    if (isNarrow()) heading.current?.focus();
  }, [threadId]);
  const title = thread.isPending ? "Opening…" : messages.at(-1)?.subject || (thread.isError && messages.length === 0 ? "This mail couldn't be opened" : "(no subject)");
  const back = mailHref({ view: params.get("view") ?? "", label: params.get("label") ?? "", q: params.get("q") ?? "" });
  return (
    <div className="p-4 sm:p-6">
      <Link to={back} onClick={onBack} className="mb-3 inline-flex items-center gap-1 text-table font-semibold text-accent xl:hidden">
        <Icon name="arrow_back" /> Back to the list
      </Link>
      {thread.isError && <Notice tone="err">{friendlyError(thread.error)}</Notice>}
      <h2 ref={heading} tabIndex={-1} className="mb-4 text-title font-semibold focus:outline-none">
        {title}
      </h2>
      <div className="space-y-3">
        {messages.map((m) => (
          <MessageView
            key={m.id}
            message={m}
            open={open.has(m.id)}
            actions={<MessageMailActions account={account} message={m} onForever={onForever} />}
            onToggle={() => {
              const next = new Set(open);
              if (next.has(m.id)) next.delete(m.id);
              else next.add(m.id);
              setOpen(next);
            }}
          />
        ))}
      </div>
    </div>
  );
}
