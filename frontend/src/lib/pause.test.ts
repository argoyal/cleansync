import { describe, expect, it } from "vitest";

import { pauseLine } from "./pause";

const base = { reason: "The model's usage limit is used up.", detail: null, since: "2026-10-13T10:00:00Z", connection_id: 1, connection_name: "Groq" };
const now = new Date("2026-10-13T12:00:00Z");

describe("pauseLine", () => {
  it("says until when a limited connection is paused", () => {
    const s = pauseLine({ ...base, kind: "limited", until: "2026-10-14T00:00:00Z" }, now);
    expect(s).toMatch(/^Groq is paused until .+: the model's usage limit is used up\.$/);
  });
  it("says when an outage is tried again", () => {
    expect(pauseLine({ ...base, kind: "down", reason: "Couldn't reach x.", until: "2026-10-13T12:05:00Z" }, now)).toMatch(
      /^Groq isn't answering: couldn't reach x\. CleanSync tries again at .+\.$/,
    );
  });
  it("points to the settings when the owner must act", () => {
    expect(pauseLine({ ...base, kind: "needs_you", reason: "The model's server refused the API key.", until: null }, now)).toBe(
      "Groq stopped: the model's server refused the API key. Check Settings → Models.",
    );
  });
});
