import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { SHOW_NEW } from "./config";
import { newButton } from "./routes";

describe("the New switch", () => {
  it("is one constant, off unless the build sets VITE_SHOW_NEW=1", () => {
    const source = readFileSync(resolve(process.cwd(), "src/config.ts"), "utf8");
    expect(source).toContain('export const SHOW_NEW: boolean = import.meta.env.VITE_SHOW_NEW === "1";');
    const docker = readFileSync(resolve(process.cwd(), "../Dockerfile"), "utf8");
    expect(docker).toContain("ARG VITE_SHOW_NEW=0");
  });

  it("is on in the tests, so New keeps its tests", () => {
    expect(SHOW_NEW).toBe(true);
    expect(newButton()).toBeDefined();
  });

  it("gives no New button when it is off", () => {
    expect(newButton(false)).toBeUndefined();
  });
});
