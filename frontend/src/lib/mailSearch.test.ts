// frontend/src/lib/mailSearch.test.ts
import { describe, expect, it } from "vitest";

import { EMPTY_SEARCH, isNumber, isWhole, readSearch, writeSearch } from "./mailSearch";

describe("the search form's words", () => {
  it("writes each field's keyword, in the form's order", () => {
    expect(
      writeSearch({
        ...EMPTY_SEARCH,
        from: "Example News",
        to: "me@example.com",
        words: "invoice",
        label: "Receipts",
        sizeOp: "smaller",
        size: "5",
        sizeUnit: "KB",
        date: "older",
        age: "2",
        ageUnit: "y",
        attachment: true,
        unread: true,
        starred: true,
      }),
    ).toBe('from:"Example News" to:me@example.com invoice label:Receipts smaller:5K older_than:2y has:attachment is:unread is:starred');
    expect(writeSearch({ ...EMPTY_SEARCH, size: "1.5", date: "newer", age: "30", ageUnit: "d" })).toBe("larger:1.5M newer_than:30d");
    expect(writeSearch({ ...EMPTY_SEARCH, date: "before", day: "2024-01-31" })).toBe("before:2024-01-31");
    expect(writeSearch({ ...EMPTY_SEARCH, date: "after", day: "2024-01-01" })).toBe("after:2024-01-01");
    expect(writeSearch({ ...EMPTY_SEARCH, date: "older", age: "" })).toBe("");
    expect(writeSearch(EMPTY_SEARCH)).toBe("");
  });

  it("reads the keywords it knows back into the fields", () => {
    const f = readSearch('from:"Example News" larger:2MB before:2024-01-31 has:attachment is:starred shoes label:receipts', ["Receipts"]);
    expect(f).toEqual({ ...EMPTY_SEARCH, from: "Example News", size: "2", sizeUnit: "MB", date: "before", day: "2024-01-31", attachment: true, starred: true, words: "shoes", label: "Receipts" });
    expect(readSearch("smaller:500k older_than:6m is:unread")).toEqual({ ...EMPTY_SEARCH, sizeOp: "smaller", size: "500", sizeUnit: "KB", date: "older", age: "6", ageUnit: "m", unread: true });
  });

  it("keeps what it can't read in Has the words", () => {
    const f = readSearch('from:a@x.com from:b@x.com "big deal" larger:2G in:archived label:Nope newer_than:1y before:2024-01-01');
    expect(f.from).toBe("a@x.com");
    expect(f.date).toBe("newer");
    expect(f.words).toBe('from:b@x.com "big deal" larger:2G in:archived label:Nope before:2024-01-01');
    expect(writeSearch(f)).toBe('from:a@x.com from:b@x.com "big deal" larger:2G in:archived label:Nope before:2024-01-01 newer_than:1y');
  });

  it("keeps repeated size keywords in the words, so the server reads the same size", () => {
    for (const text of ["larger:5M larger:3M", "larger:5M larger:2G", "smaller:1M smaller:2M"]) {
      const f = readSearch(text);
      expect(f.size).toBe("");
      expect(f.words).toBe(text);
      expect(writeSearch(f)).toBe(text);
    }
  });

  it("round-trips To and Label values with spaces, and drops quote marks", () => {
    const text = 'to:"Jane Doe" label:"My Label"';
    const f = readSearch(text, ["My Label"]);
    expect(f.to).toBe("Jane Doe");
    expect(f.label).toBe("My Label");
    expect(writeSearch(f)).toBe(text);
    expect(writeSearch({ ...EMPTY_SEARCH, from: 'Jo "Bo" Smith', to: '"', label: 'a"b' })).toBe('from:"Jo Bo Smith" label:ab');
  });

  it("checks numbers", () => {
    expect([isNumber("5"), isNumber("1.5"), isNumber(" 2 "), isNumber("abc"), isNumber("1e3")]).toEqual([true, true, true, false, false]);
    expect([isWhole("30"), isWhole("1.5"), isWhole("")]).toEqual([true, false, false]);
  });
});
