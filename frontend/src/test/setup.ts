import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { setCsrfToken } from "../api/client";
import { FakeEventSource } from "./fakeEventSource";

// Testing Library waits only 1 second by default; on a busy machine (full suite, CI) a screen that
// needs a few renders after a click can take longer, so give every wait a generous ceiling.
configure({ asyncUtilTimeout: 8000 });

class MemoryStorage implements Storage {
  private data = new Map<string, string>();
  get length() {
    return this.data.size;
  }
  clear() {
    this.data.clear();
  }
  getItem(key: string) {
    return this.data.get(key) ?? null;
  }
  key(i: number) {
    return [...this.data.keys()][i] ?? null;
  }
  removeItem(key: string) {
    this.data.delete(key);
  }
  setItem(key: string, value: string) {
    this.data.set(key, String(value));
  }
}

// Node 25 and later define their own localStorage, which hides jsdom's; tests get a working one.
for (const name of ["localStorage", "sessionStorage"] as const) {
  if (typeof globalThis[name] === "undefined") {
    Object.defineProperty(globalThis, name, { value: new MemoryStorage(), configurable: true });
  }
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  setCsrfToken("test-csrf");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  FakeEventSource.instances = [];
  localStorage.clear();
  sessionStorage.clear();
});
