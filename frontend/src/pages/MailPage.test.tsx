import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ACCOUNT, mockApi, renderApp } from "../test/utils";
import { MailPage } from "./MailPage";

describe("Mail before it is set up", () => {
  it("explains mail backup and sends the owner to Google with Gmail asked for", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [ACCOUNT],
      "POST /api/accounts/1/mail/setup": { auth_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" },
    });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    renderApp(<MailPage />, { withAccount: true, route: "/mail" });
    expect(await screen.findByRole("heading", { name: "Set up mail backup" })).toBeInTheDocument();
    expect(screen.getByText(/Nothing in Gmail changes until you choose to offload mail/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Set up mail backup" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?x=1"));
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/accounts/1/mail/setup")).toBe(true);
    expect(sessionStorage.getItem("drivesync.oauthReturn")).toBe("/mail");
  });

  it("goes straight on when no Google sign-in is needed (a pretend account)", async () => {
    mockApi({ "GET /api/accounts": [ACCOUNT], "POST /api/accounts/1/mail/setup": { auth_url: null } });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    renderApp(<MailPage />, { withAccount: true, route: "/mail?view=inbox" });
    await userEvent.click(await screen.findByRole("button", { name: "Set up mail backup" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent(/^\/mail$/));
    expect(assign).not.toHaveBeenCalled();
  });

  it("asks to reconnect when Google took the mail permission away", async () => {
    mockApi({ "GET /api/accounts": [{ ...ACCOUNT, services: { files: "active", mail: "needs_reconnect" } }] });
    renderApp(<MailPage />, { withAccount: true, route: "/mail" });
    expect(await screen.findByText(/Google no longer lets CleanSync read this account's mail/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconnect mail" })).toBeInTheDocument();
  });
});

const ACTIVE = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const }, mail_permission: true };
const STATUS = {
  account_id: 1,
  email: "alice@example.com",
  service: "active",
  phase: "incremental",
  stored: 38214,
  stored_bytes: 6.2 * 1024 ** 3,
  total: 38300,
  running: false,
  waiting: false,
  time_left_s: null,
  last_run_at: "2026-10-10T09:58:00Z",
  last_error: null,
  job: { id: 9, kind: "CHECK", state: "SUCCESS", started_at: "2026-10-10T09:58:00Z", completed_at: "2026-10-10T09:58:05Z", done: 0, message: "Nothing new" },
  failed: [],
  budget_reached: false,
};

describe("Mail backup", () => {
  it("counts the mail and starts the first backup", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [{ ...ACCOUNT, services: { files: "active", mail: "setting_up" }, mail_permission: true }],
      "GET /api/mail/setup-info": { total: 38214, size_bytes: 6657199309 },
      "POST /api/mail/start": { queued: "queued" },
    });
    renderApp(<MailPage />, { withAccount: true, route: "/mail" });
    expect(await screen.findByRole("heading", { name: "Start mail backup" })).toBeInTheDocument();
    expect(await screen.findByText("About 38,214 mails, about 6.2 GB. The first backup takes a few hours. Nothing in Gmail changes.")).toBeInTheDocument();
    expect(screen.getByText(/Gmail and Google Photos use together/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/start")?.body).toEqual({ account_id: 1 }));
  });

  it("leaves the size out when Google doesn't say", async () => {
    mockApi({
      "GET /api/accounts": [{ ...ACCOUNT, services: { files: "active", mail: "setting_up" } }],
      "GET /api/mail/setup-info": { total: 26, size_bytes: null },
    });
    renderApp(<MailPage />, { withAccount: true, route: "/mail" });
    expect(await screen.findByText("About 26 mails. The first backup takes a few hours. Nothing in Gmail changes.")).toBeInTheDocument();
  });

  it("shows the first backup's progress and roughly how long is left", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/status": { ...STATUS, phase: "initial", running: true, stored: 12345, time_left_s: 3 * 3600 },
    });
    renderApp(<MailPage />, { withAccount: true, route: "/mail" });
    expect(await screen.findByRole("heading", { name: "First backup" })).toBeInTheDocument();
    expect(screen.getByText("12,345 of about 38,300 mails · about 3 hours left")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Mails backed up" })).toHaveAttribute("aria-valuenow", "32");
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
  });

  it("says why it paused and checks again on request", async () => {
    const reason = "Paused: less than 2 GB free on the storage disk. Free some space, then press Check now.";
    const { calls } = mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/status": { ...STATUS, last_error: reason, job: { ...STATUS.job, state: "PAUSED", message: reason } },
      "POST /api/mail/check": { queued: "queued" },
    });
    renderApp(<MailPage />, { withAccount: true, route: "/mail" });
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.getByText(/^38,214 mails · 6.2 GB · checked/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check now" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/mail/check")?.body).toEqual({ account_id: 1 }));
  });

  it("lists mails that failed three times", async () => {
    mockApi({
      "GET /api/accounts": [ACTIVE],
      "GET /api/mail/status": { ...STATUS, failed: [{ provider_id: "18a0000000000007", attempts: 3, last_error: "Gmail refused the request (HTTP 500)" }] },
    });
    renderApp(<MailPage />, { withAccount: true, route: "/mail" });
    expect(await screen.findByText("1 mail couldn't be backed up after 3 tries. CleanSync tries again once a day.")).toBeInTheDocument();
    expect(screen.getByText("18a0000000000007")).toBeInTheDocument();
    expect(screen.getByText("Gmail refused the request (HTTP 500)")).toBeInTheDocument();
  });
});
