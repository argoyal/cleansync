import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

import { getTheme, setTheme, THEME_KEY } from "./theme";

// Read as plain files: files under public/ can't be imported, and CSS is not loaded in tests.
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const themeScript = read("../../public/theme.js");
const html = read("../../index.html");
const css = read("../index.css");

const root = () => document.documentElement;
const runThemeScript = () => new Function(themeScript)();

/** What a browser with blocked storage does: every call throws. */
function blockStorage() {
  const fail = () => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  };
  vi.stubGlobal("localStorage", { getItem: fail, setItem: fail, removeItem: fail, clear: fail, key: fail, length: 0 });
}

describe("theme", () => {
  it("setTheme remembers the choice and marks the page; System forgets it", () => {
    setTheme("dark");
    expect(root().getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(getTheme()).toBe("dark");
    setTheme("system");
    expect(root().hasAttribute("data-theme")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(getTheme()).toBe("system");
  });

  it("setTheme still applies the theme when storage is blocked", () => {
    blockStorage();
    expect(() => localStorage.setItem("x", "y")).toThrow(); // the stub really is in place
    expect(() => setTheme("light")).not.toThrow();
    expect(root().getAttribute("data-theme")).toBe("light");
    expect(getTheme()).toBe("system"); // nothing could be remembered
  });

  it("theme.js applies a remembered choice before the app starts", () => {
    localStorage.setItem(THEME_KEY, "dark");
    runThemeScript();
    expect(root().getAttribute("data-theme")).toBe("dark");
  });

  it("theme.js ignores anything it doesn't know", () => {
    localStorage.setItem(THEME_KEY, "purple");
    runThemeScript();
    expect(root().hasAttribute("data-theme")).toBe(false);
  });

  it("theme.js follows the system when storage is blocked", () => {
    blockStorage();
    expect(runThemeScript).not.toThrow();
    expect(root().hasAttribute("data-theme")).toBe(false);
  });

  it("index.html loads theme.js in the head, as a file (the CSP allows no inline scripts)", () => {
    const head = html.slice(0, html.indexOf("</head>"));
    expect(head).toContain('<script src="/theme.js"></script>');
    expect(html).not.toMatch(/<script>(?!<\/script>)/);
  });

  it("the picked Dark theme uses exactly the colours of the system dark theme", () => {
    const block = (start: string) => {
      const i = css.indexOf(start);
      expect(i).toBeGreaterThan(-1);
      return css.slice(i, css.indexOf("}", i));
    };
    const vars = (s: string) => s.match(/--[\w-]+:\s*[^;]+;/g) ?? [];
    const system = vars(block(':root:not([data-theme="light"])'));
    const picked = vars(block(':root[data-theme="dark"]'));
    expect(system.length).toBeGreaterThan(10);
    expect(picked).toEqual(system);
  });
});
