import { useAccounts } from "../api/queries";
import { Empty } from "../components/ui";
import { Avatar } from "../components/AccountSwitcher";
import { StorageBars } from "../components/StorageBars";

export function StoragePage() {
  const { data = [] } = useAccounts();
  return (
    <div className="space-y-6 p-4 sm:p-6">
      <h1 className="text-title font-semibold">Storage</h1>
      <p className="max-w-prose text-ink-2">
        Files in Google Drive's trash still count against your Google storage until the trash is emptied (Google does this
        after 30 days).
      </p>
      {data.length === 0 && (
        <Empty icon="storage" title="No Google account yet">
          Connect an account in Settings to see its storage.
        </Empty>
      )}
      {data.map((a) => (
        <section key={a.id} className="max-w-2xl rounded-card border border-line p-5">
          <h2 className="mb-4 flex items-center gap-3 font-semibold">
            <Avatar email={a.email} size={28} />
            {a.email}
          </h2>
          <StorageBars accountId={a.id} />
        </section>
      ))}
    </div>
  );
}
