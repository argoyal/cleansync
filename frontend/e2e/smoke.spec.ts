import { expect, test } from "@playwright/test";

const START_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "ci-smoke-password";
const NEW_PASSWORD = "a much better passphrase for e2e";
const PHRASE = "I understand and want to start synchronization.";

test("first run: log in, set up a sync, browse, upload", async ({ page }) => {
  const problems: string[] = [];
  // The app must work under its own Content-Security-Policy, and never crash. (Plain 404s are
  // expected: the preview is polled until it exists.)
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) problems.push(m.text());
  });
  page.on("pageerror", (e) => problems.push(e.message));

  // Log in with the starting password, then choose a new one.
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Password").fill(START_PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Choose your own password" })).toBeVisible();
  await page.getByLabel("Current password").fill(START_PASSWORD);
  await page.getByLabel(/^New password(?! again)/).fill(NEW_PASSWORD);
  await page.getByLabel("New password again").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Save password" }).click();

  // A new installation lands in the wizard; the seeded account skips the Google step.
  await expect(page.getByRole("heading", { name: "Choose what to sync" })).toBeVisible();
  await page.getByRole("button", { name: "Create and preview" }).click();
  await page.getByRole("button", { name: "Compare the folders" }).click();
  await expect(page.getByText("Only on Drive (download)")).toBeVisible({ timeout: 60_000 });
  await page.getByLabel(/To start, type/).fill(PHRASE);
  await page.getByRole("button", { name: "Start the first sync" }).click();
  await expect(page.getByRole("heading", { name: "Synced" })).toBeVisible({ timeout: 120_000 });

  // The files from the pretend Drive are here and marked synced.
  await page.getByRole("button", { name: "Open My Drive" }).click();
  const welcome = page.getByRole("button", { name: "Welcome.txt", exact: true });
  await expect(welcome).toBeVisible();
  await expect(welcome.getByRole("img", { name: "Synced" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Photos", exact: true }).dblclick();
  await expect(page.locator('img[src^="/api/thumbs/"]')).toBeVisible();

  // Upload a file; the app queues a sync and the badge turns to synced.
  await page.getByLabel("Files to upload").setInputFiles({
    name: "from-browser.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("uploaded by the smoke test"),
  });
  const uploaded = page.getByRole("button", { name: "from-browser.txt", exact: true });
  await expect(uploaded).toBeVisible();
  await expect(uploaded.getByRole("img", { name: "Synced" })).toBeVisible({ timeout: 90_000 });

  // Opening a text file shows it in a sandboxed tab.
  const [tab] = await Promise.all([page.waitForEvent("popup"), uploaded.dblclick()]);
  await expect(tab.locator("body")).toContainText("uploaded by the smoke test");

  expect(problems, problems.join("\n")).toEqual([]);
});
