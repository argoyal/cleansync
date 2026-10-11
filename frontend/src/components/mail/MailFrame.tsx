import { useState } from "react";

import { mailBodyUrl, useMailMessage } from "../../api/queries";
import type { MailMessageInfo } from "../../api/types";
import { Button } from "../ui";

/** Spec §6.2: the cleaned mail in a sandboxed frame (no scripts, its own origin), remote
 * images only after "Show images" (per message). Two locks keep the mail in its box: this
 * sandbox and the body route's own content-security policy. The frame always loads the
 * server-cleaned body route, never the mail's HTML as srcdoc. The white paper is on purpose:
 * mail HTML is written for white paper, in light and dark mode alike. */
export function MailFrame({ message }: { message: MailMessageInfo }) {
  const [images, setImages] = useState(false);
  // A thread list leaves has_remote_images null; the one-mail route reads the file to tell.
  const detail = useMailMessage(message.id, message.has_remote_images === null);
  const remote = message.has_remote_images ?? detail.data?.has_remote_images ?? false;
  return (
    <div className="space-y-2">
      {remote && !images && (
        <div className="flex flex-wrap items-center gap-3 rounded-ctl bg-sunken px-3 py-2 text-table">
          <span className="flex-1">Images from the internet are hidden, so the sender can't tell you opened this mail.</span>
          <Button onClick={() => setImages(true)}>Show images</Button>
        </div>
      )}
      <iframe
        title={`Mail: ${message.subject || "(no subject)"}`}
        src={mailBodyUrl(message.id, images)}
        sandbox="allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        className="h-[70vh] w-full rounded-ctl border border-line bg-white"
      />
    </div>
  );
}
