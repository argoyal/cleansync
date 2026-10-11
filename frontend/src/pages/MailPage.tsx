import { Link } from "react-router-dom";

import { MailBackupStatus } from "../components/MailBackupStatus";
import { MailSetUpCard } from "../components/MailSetUpCard";
import { MailStartCard } from "../components/MailStartCard";
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
  if (mail === "setting_up") {
    return (
      <div className="p-4 sm:p-6">
        <MailStartCard account={account} />
      </div>
    );
  }
  return (
    <div className="space-y-4 p-4 sm:p-6">
      <MailBackupStatus account={account} />
    </div>
  );
}
