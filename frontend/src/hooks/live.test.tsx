import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FakeEventSource } from "../test/fakeEventSource";
import { liveStore, progressStore, useLiveEvents } from "./live";

function Live() {
  useLiveEvents();
  return null;
}

function setup() {
  const client = new QueryClient();
  const spy = vi.spyOn(client, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={client}>
      <Live />
    </QueryClientProvider>,
  );
  return { client, spy, view, source: FakeEventSource.latest() };
}

const keys = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map((c) => (c[0] as { queryKey?: unknown[] } | undefined)?.queryKey?.[0] ?? "*");

afterEach(() => {
  progressStore.set({});
  vi.useRealTimers();
});

describe("live updates", () => {
  it("listens on /api/events and refreshes what an event touches", () => {
    const { spy, source } = setup();
    expect(source.url).toBe("/api/events");
    act(() => source.emit("files", { account_id: 1 }));
    expect(keys(spy)).toEqual(expect.arrayContaining(["browse", "recent", "starred", "search", "storage"]));
    spy.mockClear();
    act(() => source.emit("account", { account_id: 1, status: "NEEDS_REAUTH" }));
    expect(keys(spy)).toEqual(expect.arrayContaining(["accounts", "status"]));
  });

  it("keeps the latest transfer progress per pair and clears it when the job ends", () => {
    const { source } = setup();
    const p = { pair_id: 3, job_id: 9, bytes: 50, total_bytes: 100, speed: 10, eta: 5, transferring: [{ name: "a.bin" }] };
    act(() => source.emit("progress", p));
    expect(progressStore.get()[3]).toEqual(p);
    act(() => source.emit("job", { pair_id: 3, job_id: 9, state: "SUCCESS" }));
    expect(progressStore.get()[3]).toBeUndefined();
  });

  it("reconnects with a growing pause and refetches everything after a reconnect", () => {
    vi.useFakeTimers();
    const { spy, source } = setup();
    act(() => source.open());
    expect(liveStore.get()).toBe("open");
    expect(spy).not.toHaveBeenCalled();
    act(() => source.fail());
    expect(source.closed).toBe(true);
    expect(liveStore.get()).toBe("retrying");
    act(() => vi.advanceTimersByTime(1000));
    const second = FakeEventSource.latest();
    expect(second).not.toBe(source);
    act(() => second.open());
    expect(spy).toHaveBeenCalledWith(); // no key: every query
  });

  it("stops listening when the app closes", () => {
    const { view, source } = setup();
    view.unmount();
    expect(source.closed).toBe(true);
  });
});
