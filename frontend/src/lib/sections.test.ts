import { describe, expect, it } from "vitest";

import { linkActive, sectionFor, SECTIONS } from "./sections";

describe("sections", () => {
  it("lists the rail from top to bottom", () => {
    expect(SECTIONS.map((s) => s.label)).toEqual(["Files", "Mail", "Activity", "Recovery", "Settings"]);
  });

  it("knows which section a page belongs to", () => {
    for (const [path, id] of [
      ["/", "files"], ["/drive", "files"], ["/recent", "files"], ["/offloaded/pick", "files"], ["/conflicts", "files"], ["/search", "files"],
      ["/mail", "mail"], ["/mail/offloads/3", "mail"],
      ["/activity", "activity"], ["/activity/7", "activity"], ["/storage", "activity"],
      ["/recovery", "recovery"], ["/settings", "settings"],
      ["/mailbox", "files"], // only /mail and below
    ] as const) {
      expect(sectionFor(path), path).toBe(id);
    }
  });

  it("marks a view active by its path and its view, label, tab or source", () => {
    expect(linkActive("/mail", "/mail", "")).toBe(true);
    expect(linkActive("/mail", "/mail", "?view=inbox")).toBe(false);
    expect(linkActive("/mail?view=inbox", "/mail", "?view=inbox&q=x")).toBe(true);
    expect(linkActive("/offloaded", "/offloaded/pick", "")).toBe(true);
    expect(linkActive("/recovery?source=mail", "/recovery", "")).toBe(false);
    expect(linkActive("/drive", "/drivex", "")).toBe(false);
  });
});
