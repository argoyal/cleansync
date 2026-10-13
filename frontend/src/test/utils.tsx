import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";

import type { Account, FileItem, Pair } from "../api/types";
import { ToastProvider } from "../components/Toasts";
import { AccountProvider } from "../hooks/useAccount";

type Handler = (req: { url: URL; body: unknown; headers: Headers }) => unknown;
export type Call = { method: string; path: string; url: URL; body: unknown; headers: Headers };

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** Fake the API: routes are "METHOD /path" -> value or (request) => value | Response. */
export function mockApi(routes: Record<string, unknown>): { calls: Call[] } {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = (init?.method ?? "GET").toUpperCase();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      const headers = new Headers(init?.headers);
      calls.push({ method, path: url.pathname, url, body, headers });
      const route = routes[`${method} ${url.pathname}`];
      if (route === undefined) return json(404, { detail: `no mock for ${method} ${url.pathname}` });
      const out = typeof route === "function" ? await (route as Handler)({ url, body, headers }) : route;
      return out instanceof Response ? out : json(200, out ?? null);
    }),
  );
  return { calls };
}

/** For renderHook: the query client and toasts, without a router. */
export function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ToastProvider>{children}</ToastProvider>
      </QueryClientProvider>
    );
  };
}

export function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

export function renderApp(ui: ReactElement, { route = "/", path = "*", withAccount = false } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const element = withAccount ? <AccountProvider>{ui}</AccountProvider> : ui;
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <MemoryRouter initialEntries={[route]}>
            <Routes>
              <Route path={path} element={element} />
              <Route path="*" element={<LocationProbe />} />
            </Routes>
            <LocationProbe />
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>,
    ),
  };
}

export const ACCOUNT: Account = {
  id: 1,
  email: "alice@example.com",
  status: "CONNECTED",
  provider: "google",
  quota_total: 15 * 1024 ** 3,
  quota_used: 6 * 1024 ** 3,
  quota_trash: 1024 ** 3,
  connected_at: "2026-10-01T10:00:00Z",
  last_checked_at: "2026-10-08T07:00:00Z",
  services: { files: "active", mail: "not_set_up" },
  mail_permission: false,
  permanent_delete: false,
  permanent_delete_note: null,
};

export const PAIR: Pair = {
  id: 7,
  account_id: 1,
  account_email: "alice@example.com",
  name: "My Drive",
  local_subpath: "",
  local_path: "/srv/drivesync/storage/alice@example.com",
  remote_path: "",
  direction: "BIDIRECTIONAL",
  schedule_interval_s: 300,
  paused: false,
  max_deletions: 10,
  backups_enabled: true,
  backup_retention_days: 30,
  initialized: true,
  status: "HEALTHY",
  attention_reason: null,
  last_error: null,
  last_sync_at: "2026-10-08T07:55:00Z",
  last_success_at: "2026-10-08T07:55:00Z",
  last_error_at: null,
  next_sync_at: "2026-10-08T08:00:00Z",
};

export function item(over: Partial<FileItem> & { name: string }): FileItem {
  return {
    path: over.name,
    is_dir: false,
    size: 1200,
    mtime: "2026-10-08T07:00:00Z",
    status: "synced",
    local: true,
    remote: true,
    mime: "text/plain",
    native: null,
    google_url: null,
    starred: false,
    pair_id: 7,
    thumb: false,
    pair_root: false,
    ...over,
  };
}
