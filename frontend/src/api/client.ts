/** Set on file actions that stopped half way: which items changed, failed, or were left alone. */
export type PartialResult = { done: string[]; failed: string[]; not_done: string[] };

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public partial?: PartialResult,
  ) {
    super(detail);
  }
}

let csrfToken = "";

/** Sent as X-CSRF-Token on every change; comes from /api/auth/me and the login answer. */
export function setCsrfToken(token: string): void {
  csrfToken = token;
}

export function getCsrfToken(): string {
  return csrfToken;
}

/** Forget the token (on logout or a lost session). */
export function clearCsrfToken(): void {
  csrfToken = "";
}

/** For code that calls fetch directly (uploads): the header every change must carry. */
export function csrfHeaders(): Record<string, string> {
  return csrfToken ? { "X-CSRF-Token": csrfToken } : {};
}

export type AuthSignal = "login" | "change-password";
export const AUTH_EVENT = "ds:auth";

function signal(kind: AuthSignal): void {
  window.dispatchEvent(new CustomEvent<AuthSignal>(AUTH_EVENT, { detail: kind }));
}

/** A lost session or a forced password change sends people to the right page (also used by uploads). */
export function reportAuthProblem(status: number, detail: string): void {
  // Only a lost session sends people to the login page; a wrong current password is a 401 too.
  if (status === 401 && (detail === "not_logged_in" || detail === "session_expired")) signal("login");
  if (status === 403 && detail === "password_change_required") signal("change-password");
}

/** A list of paths from the server; an older answer may name a single path as plain text. */
function pathList(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === "string");
  return typeof value === "string" && value ? [value] : [];
}

export function detailOf(body: unknown, status: number): { detail: string; partial?: PartialResult } {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return { detail: d };
    if (Array.isArray(d) && d.length > 0 && typeof d[0]?.msg === "string") return { detail: d[0].msg };
    if (d && typeof d === "object" && "message" in d && typeof d.message === "string") {
      const o = d as { message: string; done?: unknown; failed?: unknown; not_done?: unknown };
      return { detail: o.message, partial: { done: pathList(o.done), failed: pathList(o.failed), not_done: pathList(o.not_done) } };
    }
  }
  return { detail: `Request failed (${status})` };
}

export async function api<T>(
  path: string,
  init: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const method = init.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      credentials: "same-origin",
      signal: init.signal,
    });
  } catch (e) {
    if (e instanceof TypeError) throw new ApiError(0, "Can't reach CleanSync. Check your connection and try again.");
    throw e;
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown;
  let parsed = true;
  try {
    body = text ? JSON.parse(text) : undefined;
  } catch {
    body = undefined;
    parsed = false;
  }
  if (!res.ok) {
    const { detail, partial } = detailOf(body, res.status);
    reportAuthProblem(res.status, detail);
    throw new ApiError(res.status, detail, partial);
  }
  if (!parsed) throw new ApiError(res.status, "Unexpected response from the server");
  return body as T;
}

const MESSAGES: Record<string, string> = {
  invalid_credentials: "Wrong username or password.",
  too_many_attempts: "Too many attempts. Wait 15 minutes, then try again.",
  current_password_wrong: "The current password is wrong.",
  csrf_check_failed: "Your session changed in another tab. Reload the page.",
  session_expired: "Your session ended. Log in again.",
  not_logged_in: "Log in to continue.",
};

/** A sentence for the person, from any error the API layer throws. */
export function friendlyError(e: unknown): string {
  if (e instanceof ApiError) {
    // The server's own 409/503/507 messages are already written for people; only the bare
    // integrity-check text gets the longer hint.
    if (e.status === 503 && /integrity/i.test(e.detail) && !/restore a backup/i.test(e.detail)) {
      return "Syncing is switched off because the database check failed. Restore a backup (see docs/backup-recovery.md).";
    }
    return MESSAGES[e.detail] ?? e.detail;
  }
  return e instanceof Error ? e.message : "Something went wrong.";
}

export function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}
