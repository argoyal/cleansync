import { describe, expect, it } from "vitest";

import { formatDuration } from "./format";

describe("formatDuration", () => {
  it("says roughly how long is left", () => {
    expect(formatDuration(20)).toBe("less than a minute");
    expect(formatDuration(60)).toBe("about 1 minute");
    expect(formatDuration(12 * 60)).toBe("about 12 minutes");
    expect(formatDuration(3 * 3600)).toBe("about 3 hours");
    expect(formatDuration(3 * 86400)).toBe("about 3 days");
  });
});
