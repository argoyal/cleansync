import { Link, useSearchParams } from "react-router-dom";

import { friendlyError } from "../api/client";
import { usePairs, useRecent, useSearch, useStarred } from "../api/queries";
import { Icon } from "../components/Icon";
import { PairStatusPill } from "../components/StatusBadge";
import { Empty, Notice } from "../components/ui";
import { useCurrentAccount } from "../hooks/useAccount";
import { formatRelative } from "../lib/format";
import { FileArea, useViewMode, ViewToggle } from "./DrivePage";

function Page({ title, children, toggle }: { title: string; children: React.ReactNode; toggle?: React.ReactNode }) {
  return (
    <div className="p-4 sm:p-6">
      <div className="mb-5 flex items-center">
        <h1 className="text-title font-semibold">{title}</h1>
        <div className="ml-auto">{toggle}</div>
      </div>
      {children}
    </div>
  );
}

export function HomePage() {
  const { accountId, account } = useCurrentAccount();
  const recent = useRecent(accountId, 8);
  const { data: pairs = [] } = usePairs();
  const mine = pairs.filter((p) => p.account_id === accountId);
  if (!account) {
    return (
      <Empty icon="cloud_done" title="Welcome">
        <Link to="/setup" className="font-semibold text-accent">
          Connect a Google account to start
        </Link>
      </Empty>
    );
  }
  return (
    <Page title={`Welcome, ${account.email}`}>
      <section className="mb-8">
        <h2 className="mb-3 text-section font-semibold">Synced folders</h2>
        {mine.length === 0 ? (
          <p className="text-ink-2">
            Nothing is synced for this account yet.{" "}
            <Link to="/setup" className="font-semibold text-accent">
              Choose folders to sync
            </Link>
          </p>
        ) : (
          <ul className="divide-y divide-line rounded-card border border-line">
            {mine.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
                <Icon name="folder" className="text-ink-2" />
                <Link to={`/drive?path=${encodeURIComponent(p.local_subpath)}`} className="font-semibold hover:underline">
                  {p.name}
                </Link>
                <PairStatusPill status={p.status} />
                <span className="ml-auto text-meta text-ink-2">Last sync {formatRelative(p.last_sync_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h2 className="mb-3 text-section font-semibold">Recently changed</h2>
        {recent.data && recent.data.length === 0 && <p className="text-ink-2">No files yet.</p>}
        {recent.data && recent.data.length > 0 && <FileArea items={recent.data} view="grid" />}
      </section>
    </Page>
  );
}

export function RecentPage() {
  const { accountId } = useCurrentAccount();
  const recent = useRecent(accountId);
  const [view, setView] = useViewMode();
  return (
    <Page title="Recent" toggle={<ViewToggle view={view} onChange={setView} />}>
      {recent.isError && <Notice tone="err">{friendlyError(recent.error)}</Notice>}
      {recent.data?.length === 0 && <Empty icon="schedule" title="Nothing changed recently" />}
      {recent.data && recent.data.length > 0 && <FileArea items={recent.data} view={view} showFolder />}
    </Page>
  );
}

export function StarredPage() {
  const { accountId } = useCurrentAccount();
  const starred = useStarred(accountId);
  const [view, setView] = useViewMode();
  return (
    <Page title="Starred" toggle={<ViewToggle view={view} onChange={setView} />}>
      {starred.isError && <Notice tone="err">{friendlyError(starred.error)}</Notice>}
      {starred.data?.length === 0 && (
        <Empty icon="star" title="No starred files">
          Star files in Google Drive; they show up here after the Drive listing refreshes.
        </Empty>
      )}
      {starred.data && starred.data.length > 0 && <FileArea items={starred.data} view={view} showFolder />}
    </Page>
  );
}

export function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const { accountId } = useCurrentAccount();
  const results = useSearch(accountId, q);
  return (
    <Page title={`Results for "${q}"`}>
      {q.trim().length < 2 && <p className="text-ink-2">Type at least 2 characters.</p>}
      {results.isError && <Notice tone="err">{friendlyError(results.error)}</Notice>}
      {results.data?.length === 0 && <Empty icon="search" title="No files match" />}
      {results.data && results.data.length > 0 && <FileArea items={results.data} view="list" showFolder />}
    </Page>
  );
}
