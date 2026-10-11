import { defineConfig, devices } from "@playwright/test";

// Runs against an already running CleanSync (CI: the built container with a seeded
// local "pretend Google" account). Set E2E_BASE_URL and E2E_ADMIN_PASSWORD.
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 30_000 },
  retries: 0,
  // The tests share one container and one login, so they run one after another.
  workers: 1,
  fullyParallel: false,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
