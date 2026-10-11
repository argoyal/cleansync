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
