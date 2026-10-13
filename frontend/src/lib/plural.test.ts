import { describe, expect, it } from "vitest";

import mailSource from "./mail.ts?raw";
import { mails } from "./plural";
import pluralSource from "./plural.ts?raw";

describe("plural", () => {
  it("says mail or mails", () => {
    expect(mails(1)).toBe("1 mail");
    expect(mails(1284)).toBe("1,284 mails");
  });

  it("lives in a file that imports nothing, so lib/mail and lib/ruleRuns don't loop", () => {
    expect(pluralSource).not.toMatch(/^import /m);
    expect(mailSource).not.toMatch(/from "\.\/ruleRuns"/);
  });
});
