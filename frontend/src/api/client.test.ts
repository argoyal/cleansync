import { describe, expect, it, vi } from "vitest";

import { json, mockApi } from "../test/utils";
import { api, ApiError, AUTH_EVENT, csrfHeaders, encodePath, friendlyError, setCsrfToken } from "./client";

describe("api client", () => {
  it("sends the CSRF token on changes only", async () => {
    const { calls } = mockApi({ "GET /api/x": { ok: 1 }, "POST /api/x": { ok: 2 } });
    setCsrfToken("tok-1");
    await api("/api/x");
    await api("/api/x", { method: "POST", body: { a: 1 } });
    expect(calls[0].headers.get("X-CSRF-Token")).toBeNull();
    expect(calls[1].headers.get("X-CSRF-Token")).toBe("tok-1");
    expect(calls[1].body).toEqual({ a: 1 });
  });

  it("turns error answers into ApiError with the server's detail", async () => {
    mockApi({ "POST /api/x": () => json(409, { detail: "this pair is busy" }) });
    await expect(api("/api/x", { method: "POST" })).rejects.toEqual(new ApiError(409, "this pair is busy"));
  });

  it("reads FastAPI validation errors", async () => {
    mockApi({ "POST /api/x": () => json(422, { detail: [{ msg: "field required" }] }) });
    await expect(api("/api/x", { method: "POST" })).rejects.toMatchObject({ detail: "field required" });
  });

  it("reads the message and item lists of partly done file actions", async () => {
    mockApi({
      "POST /api/files/move": () =>
        json(409, { detail: { message: "name already exists", done: ["a"], failed: ["b"], not_done: ["c"] } }),
    });
    const err = (await api("/api/files/move", { method: "POST" }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.detail).toBe("name already exists");
    expect(err.partial).toEqual({ done: ["a"], failed: ["b"], not_done: ["c"] });
  });

  it("signals a lost session and a required password change", async () => {
    const seen: string[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(AUTH_EVENT, listener);
    mockApi({
      "GET /api/a": () => json(401, { detail: "session_expired" }),
      "GET /api/b": () => json(403, { detail: "password_change_required" }),
      "POST /api/auth/login": () => json(401, { detail: "invalid_credentials" }),
    });
    await api("/api/a").catch(() => undefined);
    await api("/api/b").catch(() => undefined);
    await api("/api/auth/login", { method: "POST", body: {} }).catch(() => undefined);
    window.removeEventListener(AUTH_EVENT, listener);
    expect(seen).toEqual(["login", "change-password"]);
  });

  it("gives people readable messages", () => {
    expect(friendlyError(new ApiError(401, "invalid_credentials"))).toBe("Wrong username or password.");
    expect(friendlyError(new ApiError(503, "database integrity check failed"))).toMatch(/Restore a backup/);
    expect(friendlyError(new ApiError(503, "database integrity check failed; restore a backup (see docs/x.md)"))).toBe(
      "database integrity check failed; restore a backup (see docs/x.md)",
    );
    expect(friendlyError(new ApiError(507, "the server disk is full"))).toBe("the server disk is full");
    expect(friendlyError(new ApiError(409, "Something specific"))).toBe("Something specific");
    expect(friendlyError(new Error("boom"))).toBe("boom");
  });

  it("encodes each path part", () => {
    expect(encodePath("Work/a b#1?.txt")).toBe("Work/a%20b%231%3F.txt");
  });

  it("does not treat a wrong current password as a lost session", async () => {
    const seen: string[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(AUTH_EVENT, listener);
    mockApi({ "POST /api/auth/change-password": () => json(401, { detail: "current_password_wrong" }) });
    await expect(api("/api/auth/change-password", { method: "POST", body: {} })).rejects.toMatchObject({
      status: 401,
      detail: "current_password_wrong",
    });
    window.removeEventListener(AUTH_EVENT, listener);
    expect(seen).toEqual([]);
  });

  it("explains network failures and leaves aborts alone", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/api/x")).rejects.toMatchObject({
      detail: "Can't reach CleanSync. Check your connection and try again.",
    });
    const abort = new DOMException("aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abort));
    await expect(api("/api/x")).rejects.toBe(abort);
  });

  it("refuses a success answer that is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>oops</html>", { status: 200 })));
    await expect(api("/api/x")).rejects.toEqual(new ApiError(200, "Unexpected response from the server"));
  });

  it("gives direct fetch calls (uploads) the CSRF header", () => {
    setCsrfToken("tok-9");
    expect(csrfHeaders()).toEqual({ "X-CSRF-Token": "tok-9" });
    setCsrfToken("");
    expect(csrfHeaders()).toEqual({});
  });
});
