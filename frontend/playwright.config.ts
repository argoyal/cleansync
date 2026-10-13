import { defineConfig, devices } from "@playwright/test";

// Runs against an already running CleanSync (CI: the built container with a seeded
// local "pretend Google" account). Set E2E_BASE_URL and E2E_ADMIN_PASSWORD. The rules test also
// needs the pretend model (DRIVESYNC_ALLOW_LOCAL_ACCOUNTS=1 python -m app.devtools.fake_model, or
// make fake-model) where the server can reach it;
// E2E_FAKE_MODEL_URL says where, by default http://127.0.0.1:11999/v1.
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
  projects: [
    { name: "chromium", testMatch: "smoke.spec.ts", use: { ...devices["Desktop Chrome"] } },
    // After the smoke tests: the rules test changes the sample mailbox they count.
    { name: "rules", testMatch: "rules.spec.ts", dependencies: ["chromium"], use: { ...devices["Desktop Chrome"] } },
    // After the rules test: it removes mail from the pretend Gmail for good, then restores it.
    { name: "permanent-delete", testMatch: "permanent-delete.spec.ts", dependencies: ["rules"], use: { ...devices["Desktop Chrome"] } },
  ],
});
