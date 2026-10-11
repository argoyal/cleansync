import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { friendlyError } from "../../api/client";
import { useMailThread } from "../../api/queries";
import type { Account } from "../../api/types";
import { mailHref } from "../../lib/mail";
import { Icon } from "../Icon";
import { Notice } from "../ui";
import { MessageView } from "./MessageView";

export function MailThread({ account, threadId }: { account: Account; threadId: string }) {
  const [params] = useSearchParams();
  const thread = useMailThread(account.id, threadId);
  const messages = thread.data ?? [];
  const [open, setOpen] = useState<Set<number>>(new Set());
  const newest = messages.at(-1)?.id;
  useEffect(() => setOpen(new Set(newest !== undefined ? [newest] : [])), [threadId, newest]);
  const back = mailHref({ view: params.get("view") ?? "", label: params.get("label") ?? "", q: params.get("q") ?? "" });
  return (
    <div className="p-4 sm:p-6">
      <Link to={back} className="mb-3 inline-flex items-center gap-1 text-table font-semibold text-accent xl:hidden">
        <Icon name="arrow_back" /> Back to the list
      </Link>
      {thread.isError && <Notice tone="err">{friendlyError(thread.error)}</Notice>}
      <h2 className="mb-4 text-title font-semibold">{messages.at(-1)?.subject || (thread.isSuccess ? "(no subject)" : "")}</h2>
      <div className="space-y-3">
        {messages.map((m) => (
          <MessageView
            key={m.id}
            message={m}
            open={open.has(m.id)}
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
