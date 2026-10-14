import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../../pages/SettingsPage";
import { AppRoutes } from "../../routes";
import { ACCOUNT, mockApi, PAIR, renderApp } from "../../test/utils";

const MAILBOX = { ...ACCOUNT, services: { files: "active" as const, mail: "active" as const } };
const HOME = {
  "GET /api/health": { status: "ok", version: "0.1.0", app_name: "CleanSync" },
  "GET /api/sync-pairs": [PAIR],
  "GET /api/setup": { oauth_configured: true, redirect_uri: "x", public_url: "x", insecure_http: false, accounts: 1, pairs: 1, initialized_pairs: 1 },
};

describe("Allow Gmail filters", () => {
  it("turns on through Google's sign-in", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const { calls } = mockApi({
      "GET /api/accounts": [MAILBOX],
      "POST /api/accounts/1/gmail-filters": { auth_url: "https://accounts.google.com/filters" },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    const box = await screen.findByRole("group", { name: "Allow Gmail filters" });
    expect(box).toHaveTextContent("Allow Gmail filters: Off");
    expect(box).toHaveTextContent("Turn this on to let CleanSync keep a sender's new mail out of your Inbox when it can't unsubscribe for you.");
    await userEvent.click(within(box).getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://accounts.google.com/filters"));
    expect(calls.find((c) => c.path === "/api/accounts/1/gmail-filters")?.body).toEqual({ on: true });
  });

  it("says what On means, how to take it back at Google, and turns it off", async () => {
    const { calls } = mockApi({
      "GET /api/accounts": [{ ...MAILBOX, gmail_filters: true }],
      "POST /api/accounts/1/gmail-filters": { auth_url: null },
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    const box = await screen.findByRole("group", { name: "Allow Gmail filters" });
    expect(box).toHaveTextContent("Allow Gmail filters: On");
    expect(box).toHaveTextContent("CleanSync can make a Gmail filter that keeps a sender's new mail out of your Inbox.");
    expect(box).toHaveTextContent("myaccount.google.com/permissions");
    await userEvent.click(within(box).getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/api/accounts/1/gmail-filters")?.body).toEqual({ on: false }));
  });

  it("shows why Gmail filters went off, and nothing for a mailbox without mail backup", async () => {
    mockApi({
      "GET /api/accounts": [
        { ...MAILBOX, gmail_filters_note: "Gmail filters were turned off: Google no longer allows them for this mailbox." },
        { ...ACCOUNT, id: 2, email: "bob@example.com" },
      ],
    });
    renderApp(<SettingsPage />, { route: "/settings?tab=accounts", path: "/settings" });
    expect(await screen.findByText("Gmail filters were turned off: Google no longer allows them for this mailbox.")).toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: "Allow Gmail filters" })).toHaveLength(1);
  });

  it("says Gmail filters are on after Google's sign-in", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/settings?tab=accounts");
    mockApi({ ...HOME, "GET /api/accounts": [ACCOUNT] });
    renderApp(<AppRoutes />, { route: "/?connected=1&filters=on" });
    expect(await screen.findByText("Gmail filters are on for this mailbox.")).toBeInTheDocument();
  });

  it("says Google didn't allow Gmail filters", async () => {
    sessionStorage.setItem("drivesync.oauthReturn", "/settings?tab=accounts");
    mockApi({ ...HOME, "GET /api/accounts": [ACCOUNT] });
    renderApp(<AppRoutes />, { route: "/?connected=1&filters=denied" });
    expect(await screen.findByText("Google didn't allow Gmail filters, so they stay off. Try again and tick the box for it.")).toBeInTheDocument();
  });
});
