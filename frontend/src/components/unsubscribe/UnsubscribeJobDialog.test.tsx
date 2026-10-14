import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { mockApi, renderApp } from "../../test/utils";
import { UnsubscribeJobDialog } from "./UnsubscribeJobDialog";

const JOB = {
  id: 5,
  account_id: 1,
  kind: "unsubscribe",
  state: "RUNNING",
  total: 6,
  done: 5,
  stop_asked: false,
  reason: null,
  created_at: "2026-10-14T08:00:00Z",
  finished_at: null,
  items: [
    { address: "a@x.example", outcome: "unsubscribed", method: "one_click", note: "Unsubscribed (one-click).", link: "https://x.example/u" },
    { address: "b@x.example", outcome: "auto_archived", method: "filter", note: null, link: null },
    { address: "c@x.example", outcome: "kept", method: null, note: null, link: null },
    { address: "d@x.example", outcome: "not_confirmed", method: null, note: "No model to check the page. Open it to finish.", link: "https://d.example/u?t=1" },
    { address: "e@x.example", outcome: "needs_you", method: null, note: "Write to this address to unsubscribe, or turn on Allow Gmail filters.", link: "mailto:off@e.example" },
    { address: "f@x.example", outcome: null, method: null, note: null, link: null },
  ],
};

describe("the unsubscribe job window", () => {
  it("shows each sender's outcome in words, with its note", async () => {
    mockApi({ "GET /api/mail/unsubscribe/jobs/5": JOB });
    renderApp(<UnsubscribeJobDialog accountId={1} jobId={5} onClose={() => undefined} />);
    expect(await screen.findByText("5 of 6 done")).toBeInTheDocument();
    expect(screen.getByText("a@x.example: Unsubscribed (Unsubscribed (one-click).)")).toBeInTheDocument();
    expect(screen.getByText("b@x.example: Auto-archived")).toBeInTheDocument();
    expect(screen.getByText("c@x.example: Kept")).toBeInTheDocument();
    expect(screen.getByText("d@x.example: Couldn't confirm (No model to check the page. Open it to finish.)")).toBeInTheDocument();
    expect(screen.getByText(/^e@x\.example: Needs you/)).toBeInTheDocument();
  });

  it("offers Open page for a web link and Write to it for a mail address", async () => {
    mockApi({ "GET /api/mail/unsubscribe/jobs/5": JOB });
    renderApp(<UnsubscribeJobDialog accountId={1} jobId={5} onClose={() => undefined} />);
    const open = await screen.findByRole("link", { name: "Open page" });
    expect(open).toHaveAttribute("href", "https://d.example/u?t=1");
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Write to it" })).toHaveAttribute("href", "mailto:off@e.example");
    expect(screen.getAllByRole("link")).toHaveLength(2); // nothing for the done ones
  });

  it("never links a link that isn't web or mail", async () => {
    mockApi({
      "GET /api/mail/unsubscribe/jobs/5": { ...JOB, items: [{ address: "z@x.example", outcome: "not_confirmed", method: null, note: null, link: "javascript:alert(1)" }] },
    });
    renderApp(<UnsubscribeJobDialog accountId={1} jobId={5} onClose={() => undefined} />);
    await screen.findByText(/z@x\.example/);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("Stop asks the server to stop the job", async () => {
    const { calls } = mockApi({ "GET /api/mail/unsubscribe/jobs/5": JOB, "POST /api/mail/unsubscribe/jobs/5/stop": { ...JOB, stop_asked: true } });
    renderApp(<UnsubscribeJobDialog accountId={1} jobId={5} onClose={() => undefined} />);
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/mail/unsubscribe/jobs/5/stop")).toBe(true));
  });

  it("Mark unsubscribed saves the sender's status", async () => {
    const { calls } = mockApi({ "GET /api/mail/unsubscribe/jobs/5": JOB, "POST /api/mail/unsubscribe/mark": { changed: 1 } });
    renderApp(<UnsubscribeJobDialog accountId={1} jobId={5} onClose={() => undefined} />);
    const marks = await screen.findAllByRole("button", { name: "Mark unsubscribed" });
    await userEvent.click(marks[0]);
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/mail/unsubscribe/mark")?.body).toEqual({ account_id: 1, addresses: ["d@x.example"], status: "unsubscribed" }),
    );
  });
});
