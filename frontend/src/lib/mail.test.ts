import { describe, expect, it } from "vitest";

import { labelDot, LABEL_DOTS, mailHref, viewTitle } from "./mail";

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
});
