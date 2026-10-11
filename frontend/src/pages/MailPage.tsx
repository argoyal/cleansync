import { Link } from "react-router-dom";

import { MailSetUpCard } from "../components/MailSetUpCard";
import { Empty } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";

/** The Mail section. Plan 2 adds the backup screens; Plan 3 the reader. */
export function MailPage() {
  const { account } = useCurrentAccount();
  if (!account) {
    return (
      <Empty icon="mail" title="No Google account yet">
        <Link to="/setup" className="font-semibold text-accent">
          Connect a Google account to start
        </Link>
      </Empty>
    );
  }
  const mail = account.services.mail;
  if (mail === "not_set_up" || mail === "needs_reconnect") {
    return (
      <div className="p-4 sm:p-6">
        <MailSetUpCard account={account} reconnect={mail === "needs_reconnect"} />
      </div>
    );
  }
  return (
    <div className="p-4 sm:p-6">
      <h1 className="text-title font-semibold">Mail</h1>
      <p className="mt-2 text-ink-2">Gmail permission is in place for {account.email}.</p>
    </div>
  );
}
