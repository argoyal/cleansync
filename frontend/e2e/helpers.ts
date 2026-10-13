import { execFileSync } from "node:child_process";
import fs from "node:fs";

import { expect, type Page } from "@playwright/test";

// Shared by the browser tests: they run one after another against one CleanSync, and each can
// also run on its own, so ready() does the first-run steps when they haven't happened yet.

export const START_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "ci-smoke-password";
const NEW_PASSWORD = "a much better passphrase for e2e";
const PHRASE = "I understand and want to start synchronization.";

export async function logIn(page: Page, password: string) {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).not.toHaveURL(/\/login$/); // logged in before going anywhere else
}

/** Change the starting password: a new installation asks for that first. */
export async function choosePassword(page: Page) {
  await expect(page.getByRole("heading", { name: "Choose your own password" })).toBeVisible();
  await page.getByLabel("Current password").fill(START_PASSWORD);
  await page.getByLabel(/^New password(?! again)/).fill(NEW_PASSWORD);
  await page.getByLabel("New password again").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Save password" }).click();
}

/** The wizard's first sync of the seeded pretend account, from "Choose what to sync" to "Synced". */
export async function firstSync(page: Page) {
  await expect(page.getByRole("heading", { name: "Choose what to sync" })).toBeVisible();
  await page.getByRole("button", { name: "Create and preview" }).click();
  await page.getByRole("button", { name: "Compare the folders" }).click();
  await expect(page.getByText("Only on Drive (download)")).toBeVisible({ timeout: 60_000 });
  await page.getByLabel(/To start, type/).fill(PHRASE);
  await page.getByRole("button", { name: "Start the first sync" }).click();
  await expect(page.getByRole("heading", { name: "Synced" })).toBeVisible({ timeout: 120_000 });
}

/** Logged in, with the first sync done, whichever tests ran before. */
export async function ready(page: Page) {
  const tryNew = await page.request.post("/api/auth/login", { data: { username: "admin", password: NEW_PASSWORD } });
  if (!tryNew.ok()) {
    // A new installation: the starting password, then the wizard.
    await logIn(page, START_PASSWORD);
    await choosePassword(page);
    await firstSync(page);
  } else {
    const pairs = await page.request.get("/api/sync-pairs");
    expect(pairs.ok(), await pairs.text()).toBe(true);
    if (!((await pairs.json()) as { initialized: boolean }[]).some((p) => p.initialized)) {
      await page.goto("/setup");
      await firstSync(page);
    }
  }
  await page.goto("/drive");
  await expect(page.getByRole("button", { name: "Welcome.txt", exact: true })).toBeVisible();
}

/** Mail set up for alice and its first backup done (the sample mailbox: 26 kept of 28). */
export async function setUpMail(page: Page) {
  await page.goto("/mail");
  const status = page.getByText(/^26 mails · .* · checked/);
  const setUp = page.getByRole("button", { name: "Set up mail backup" }).first();
  await expect(status.or(setUp)).toBeVisible(); // wait for the page to settle before choosing
  if (await status.isVisible()) return;
  await setUp.click();
  await expect(page.getByRole("heading", { name: "Start mail backup" })).toBeVisible();
  await expect(page.getByText("About 28 mails. The first backup takes a few hours. Nothing in Gmail changes.")).toBeVisible();
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(status).toBeVisible({ timeout: 120_000 });
}

/** Every mail the pretend Gmail holds, with its labels. Needs E2E_CONTAINER (the container's
 * name) to look inside, or E2E_FAKE_GMAIL (the mailbox file, for a CleanSync run without Docker);
 * without either this returns null and the test skips that check (in CI it throws). */
export function fakeGmailMails(): { subject: string; labels: string[] }[] | null {
  const name = process.env.E2E_CONTAINER;
  const file = process.env.E2E_FAKE_GMAIL;
  if (!name && !file) {
    // In CI a missing name would silently skip the Gmail checks, so fail instead.
    if (process.env.CI) throw new Error("E2E_CONTAINER must name the container in CI, or the trash checks are skipped");
    return null;
  }
  const json = name
    ? execFileSync("docker", ["exec", name, "cat", "/drivesync/app/data/fake-gmail/alice@example.com.json"], {
        encoding: "utf8",
        maxBuffer: 256 * 1024 * 1024,
      })
    : fs.readFileSync(file!, "utf8");
  const data = JSON.parse(json) as { messages: { labels: string[]; raw: string }[] };
  return data.messages.map((m) => ({
    labels: m.labels,
    subject: /^Subject: (.*)$/m.exec(Buffer.from(m.raw, "base64").toString("utf8"))?.[1]?.trim() ?? "",
  }));
}

/** Subjects of the mails the pretend Gmail holds in its trash (null: see fakeGmailMails). */
export function fakeGmailTrash(): string[] | null {
  const mails = fakeGmailMails();
  return mails && mails.filter((m) => m.labels.includes("TRASH")).map((m) => m.subject);
}

/** Deletes mail in the pretend Gmail by subject, as if the owner deleted it on their phone.
 * Like fakeGmailTrash it needs E2E_CONTAINER or E2E_FAKE_GMAIL (with E2E_FAKE_GMAIL the
 * backend's own Python runs it, from ../backend); without either it returns false and the
 * test skips that part. */
export function fakeGmailDelete(subject: string): boolean {
  const name = process.env.E2E_CONTAINER;
  const file = process.env.E2E_FAKE_GMAIL;
  if (!name && !file) {
    if (process.env.CI) throw new Error("E2E_CONTAINER must name the container in CI, or the Deleted in Gmail step is skipped");
    return false;
  }
  const args = ["-m", "app.devtools.fake_gmail", "delete", "--file", name ? "/drivesync/app/data/fake-gmail/alice@example.com.json" : file!, "--subject", subject];
  if (name) execFileSync("docker", ["exec", name, "python", ...args]);
  else execFileSync("uv", ["run", "python", ...args], { cwd: process.env.E2E_BACKEND_DIR ?? "../backend" });
  return true;
}
