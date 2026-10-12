import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatWhen } from "../lib/format";
import { ACCOUNT, mockApi, renderApp } from "../test/utils";
import { MailBackupStatus } from "./MailBackupStatus";

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
  job: null,
  failed: [],
  budget_reached: false,
};
const BASE = { enabled: true, waiting: 4, sorted_today: 212, suggested: 17, failing_since: null, last_error: null, warning: false, model_set: true };

describe("sorting status", () => {
  it("shows today's sorting", async () => {
    mockApi({ "GET /api/mail/status": STATUS, "GET /api/rules/sorting": BASE });
    renderApp(<MailBackupStatus account={ACCOUNT} />);
    expect(await screen.findByText(/212 today · 4 waiting/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "17 suggestions" })).toHaveAttribute("href", "/mail?view=suggested");
  });

  it("warns after an hour of failures", async () => {
    const since = "2026-10-12T10:40:00Z";
    mockApi({ "GET /api/mail/status": STATUS, "GET /api/rules/sorting": { ...BASE, warning: true, failing_since: since, last_error: "Couldn't reach the model." } });
    renderApp(<MailBackupStatus account={ACCOUNT} />);
    expect(await screen.findByText(/Couldn't reach the model/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`failing since ${formatWhen(since)}`))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings → Models" })).toHaveAttribute("href", "/settings?tab=models");
  });

  it("shows nothing when sorting is off", async () => {
    mockApi({ "GET /api/mail/status": STATUS, "GET /api/rules/sorting": { ...BASE, enabled: false } });
    renderApp(<MailBackupStatus account={ACCOUNT} />);
    await screen.findByText(/38,214 mails/);
    expect(screen.queryByText(/Sorting/)).not.toBeInTheDocument();
  });
});
