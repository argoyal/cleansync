import { describe, expect, it } from "vitest";

import { CLEANSYNC_VIEWS, GMAIL_VIEWS, labelDot, LABEL_DOTS, mailHref, mailView, viewKind, viewTitle } from "./mail";

describe("mail helpers", () => {
  it("names each view", () => {
    expect(viewTitle("all")).toBe("All mail");
    expect(viewTitle("deleted")).toBe("Deleted in Gmail");
    expect(viewTitle("all", "Receipts")).toBe("Receipts");
    expect(viewTitle("nonsense")).toBe("All mail");
  });

  it("keeps a label's colour stable", () => {
    expect(labelDot("Label_1")).toBe(labelDot("Label_1"));
    expect(LABEL_DOTS).toContain(labelDot("Label_42"));
  });

  it("builds Mail addresses without empty parts", () => {
    expect(mailHref({})).toBe("/mail");
    expect(mailHref({ view: "all" })).toBe("/mail");
    expect(mailHref({ view: "inbox", thread: "t1" })).toBe("/mail?view=inbox&thread=t1");
    expect(mailHref({ label: "Label_2", q: "from:bank" })).toBe("/mail?label=Label_2&q=from%3Abank");
  });

  it("lists Gmail's views first and CleanSync's own after", () => {
    expect(GMAIL_VIEWS.map((v) => v.label)).toEqual(["Inbox", "Starred", "Important", "Sent", "Archived", "All mail"]);
    expect(CLEANSYNC_VIEWS.map((v) => v.label)).toEqual(["Suggested", "Offloaded", "Deleted in Gmail"]);
    expect(viewTitle("archived")).toBe("Archived");
  });

  it("knows which views hold mail in Gmail", () => {
    expect(["all", "inbox", "archived", "sent"].map(viewKind)).toEqual(["gmail", "gmail", "gmail", "gmail"]);
    expect(["offloaded", "deleted", "suggested"].map(viewKind)).toEqual(["out", "out", "suggested"]);
  });

  it("opens Offloaded for the old Local archive", () => {
    expect(mailView(null)).toBe("all");
    expect(mailView("archive")).toBe("offloaded");
    expect(mailView("inbox")).toBe("inbox");
    expect(mailHref({ view: "offloaded", tab: "batches" })).toBe("/mail?view=offloaded&tab=batches");
  });
});
