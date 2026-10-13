import { describe, expect, it } from "vitest";

import { actionText, approveText, confirmLabel, heavyKinds, olderMailScope, ruleLine, runBlock, wouldText } from "./ruleRuns";

describe("running rules, in words", () => {
  it("names one action and the count, else Apply N changes", () => {
    expect(approveText([{ actions: [{ kind: "archive" }], picked: 412 }])).toBe("Archive 412 mails");
    expect(approveText([{ actions: [{ kind: "label", label: "Receipts" }], picked: 1 }])).toBe("Add the label Receipts to 1 mail");
    expect(approveText([{ actions: [{ kind: "label", label: "Marketing" }, { kind: "archive" }], picked: 2 }])).toBe("Apply 2 changes");
    expect(approveText([{ actions: [{ kind: "archive" }], picked: 412 }, { actions: [{ kind: "star" }], picked: 30 }])).toBe("Apply 442 changes");
    expect(approveText([{ actions: [{ kind: "archive" }], picked: 3 }, { actions: [{ kind: "star" }], picked: 0 }])).toBe("Archive 3 mails");
    expect(approveText([{ actions: [{ kind: "archive" }], picked: 0 }])).toBe("Archive 0 mails");
    expect(actionText({ kind: "mark_read" }, 5)).toBe("Mark 5 mails read");
    expect(actionText({ kind: "star" }, 1)).toBe("Star 1 mail");
    expect(actionText({ kind: "offload" }, 120)).toBe("Offload 120 mails");
    expect(actionText({ kind: "delete_forever" }, 2)).toBe("Delete 2 mails forever");
  });

  it("says what a rule would do", () => {
    expect(wouldText("Marketing", [{ kind: "archive" }], 412)).toBe("Marketing would archive 412 mails.");
    expect(wouldText("Marketing", [{ kind: "label", label: "Marketing" }, { kind: "archive" }], 2)).toBe("Marketing would change 2 mails: Label Marketing, Archive.");
    expect(ruleLine("Marketing", [{ kind: "archive" }], 412)).toBe("Marketing · archive · 412");
    expect(ruleLine("Receipts", [{ kind: "label", label: "Receipts" }], 30)).toBe("Receipts · label Receipts · 30");
  });

  it("asks for the typed count only for picked Offload and Delete forever", () => {
    expect(heavyKinds([{ actions: [{ kind: "archive" }], picked: 9 }])).toEqual({ count: 0, kinds: new Set() });
    const h = heavyKinds([
      { actions: [{ kind: "offload" }], picked: 120 },
      { actions: [{ kind: "archive" }], picked: 5 },
      { actions: [{ kind: "delete_forever" }], picked: 0 },
    ]);
    expect(h).toEqual({ count: 120, kinds: new Set(["offload"]) });
    expect(confirmLabel(h.kinds, h.count)).toBe("Type 120 to offload these mails");
    expect(confirmLabel(new Set(["delete_forever"]), 1)).toBe("Type 1 to delete these mails forever");
    expect(confirmLabel(new Set(["offload", "delete_forever"]), 3)).toBe("Type 3 to offload or delete forever these mails");
  });

  it("runs a rule made from mail on its sender, the list on screen, or the Inbox", () => {
    expect(olderMailScope(["shop@example.com"], "inbox", "", "")).toEqual({ view: "all", q: "from:shop@example.com" });
    expect(olderMailScope(["a@x.com", "b@x.com"], "all", "Label_2", "older_than:1y")).toEqual({ view: "all", label: "Label_2", q: "older_than:1y", pick_all: true });
    expect(olderMailScope([], "archived", "", "")).toEqual({ view: "archived", q: "", pick_all: true });
    expect(olderMailScope([], "all", "", "")).toEqual({ inbox: true });
    expect(olderMailScope([], "suggested", "", "x")).toEqual({ inbox: true });
    expect(olderMailScope([], "offloaded", "", "x")).toEqual({ inbox: true });
  });

  it("says why a rule can't run", () => {
    expect(runBlock({ enabled: false, combine: "fixed_only" }, true)).toBe("Turn the rule on to run it.");
    expect(runBlock({ enabled: true, combine: "ai_only" }, false)).toBe("Set up a model in Settings → Models.");
    expect(runBlock({ enabled: true, combine: "fixed_then_ai" }, false)).toBe("Set up a model in Settings → Models.");
    expect(runBlock({ enabled: true, combine: "fixed_only" }, false)).toBeNull();
    expect(runBlock({ enabled: true, combine: "ai_only" }, true)).toBeNull();
  });
});
