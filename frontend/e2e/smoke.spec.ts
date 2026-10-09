import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

// The tests share one container and one login: the second one starts where the first ends.
test.describe.configure({ mode: "serial" });

const START_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "ci-smoke-password";
const NEW_PASSWORD = "a much better passphrase for e2e";
const PHRASE = "I understand and want to start synchronization.";
const PRETEND_NATIVE_MSG = "Google Docs, Sheets and Slides need a real Google account.";
const NOT_SYNCED_NOTE = "This folder isn't synced with Google Drive. Open a synced folder to add files.";

async function logIn(page: Page, password: string) {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).not.toHaveURL(/\/login$/); // logged in before going anywhere else
}

async function openNew(page: Page) {
  await page.getByRole("button", { name: "New", exact: true }).click();
}

async function newItem(page: Page, name: string, sub?: string) {
  await openNew(page);
  if (sub) {
    await page.getByRole("menuitem", { name: sub }).click();
  }
  await page.getByRole("menuitem", { name, exact: true }).click();
}

async function expectSynced(page: Page, name: string) {
  const item = page.getByRole("button", { name, exact: true });
  await expect(item).toBeVisible();
  await expect(item.getByRole("img", { name: "Synced" })).toBeVisible({ timeout: 90_000 });
}

/** Downloads a file the way the browser's Download does and returns its bytes. */
async function download(page: Page, accountId: number, rel: string): Promise<Buffer> {
  const r = await page.request.get(`/files/${accountId}/${encodeURIComponent(rel)}?download=true`);
  expect(r.status(), `download ${rel}`).toBe(200);
  expect(r.headers()["content-disposition"]).toContain("attachment");
  return r.body();
}

test("first run: log in, set up a sync, browse, upload", async ({ page }) => {
  const problems: string[] = [];
  // The app must work under its own Content-Security-Policy, and never crash. (Plain 404s are
  // expected: the preview is polled until it exists.)
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) problems.push(m.text());
  });
  page.on("pageerror", (e) => problems.push(e.message));

  // Log in with the starting password, then choose a new one.
  await logIn(page, START_PASSWORD);
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

test("every + New item works in the built image", async ({ page }) => {
  test.setTimeout(600_000); // each item waits for its own sync to Drive
  const problems: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) problems.push(m.text());
  });
  page.on("pageerror", (e) => problems.push(e.message));

  await logIn(page, NEW_PASSWORD); // the first test changed the password
  await page.goto("/drive");
  await expect(page.getByRole("button", { name: "Welcome.txt", exact: true })).toBeVisible();
  const accounts = (await (await page.request.get("/api/accounts")).json()) as { id: number; email: string }[];
  const alice = accounts.find((a) => a.email === "alice@example.com");
  expect(alice).toBeDefined();
  const aliceId = alice!.id;

  // New folder
  await newItem(page, "New folder");
  await page.getByLabel("Folder name").fill("Made by e2e");
  await page.getByRole("button", { name: "Create" }).click();
  await expectSynced(page, "Made by e2e");

  // Upload files
  await page.getByLabel("Files to upload").setInputFiles({
    name: "e2e-upload.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("from the New menu test"),
  });
  await expectSynced(page, "e2e-upload.txt");

  // Upload folder: a real folder on disk, picked like a person would.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ds-e2e-"));
  const trip = path.join(tmp, "Trip");
  fs.mkdirSync(path.join(trip, "day 1"), { recursive: true });
  fs.writeFileSync(path.join(trip, "day 1", "notes.txt"), "a folder upload");
  await page.getByLabel("Folder to upload").setInputFiles(trip);
  await expectSynced(page, "Trip");
  await page.getByRole("button", { name: "Trip", exact: true }).dblclick();
  await expectSynced(page, "day 1");
  await page.getByRole("button", { name: "day 1", exact: true }).dblclick();
  await expectSynced(page, "notes.txt");
  expect((await download(page, aliceId, "Trip/day 1/notes.txt")).toString()).toBe("a folder upload");
  await page.goto("/drive");
  await expect(page.getByRole("button", { name: "Welcome.txt", exact: true })).toBeVisible();

  // Word, Excel and PowerPoint files from the built-in templates: real Office files (zip
  // packages with the right main part) that download.
  const office = [
    { item: "Word file (.docx)", sub: "Google Docs", name: "Untitled document.docx", part: "word/document.xml" },
    { item: "Excel file (.xlsx)", sub: "Google Sheets", name: "Untitled spreadsheet.xlsx", part: "xl/workbook.xml" },
    { item: "PowerPoint file (.pptx)", sub: "Google Slides", name: "Untitled presentation.pptx", part: "ppt/presentation.xml" },
  ];
  for (const f of office) {
    await newItem(page, f.item, f.sub);
    await expectSynced(page, f.name);
    const bytes = await download(page, aliceId, f.name);
    expect(bytes.subarray(0, 2).toString(), `${f.name} is a zip package`).toBe("PK");
    expect(bytes.includes(Buffer.from("[Content_Types].xml")), `${f.name} has content types`).toBe(true);
    expect(bytes.includes(Buffer.from(f.part)), `${f.name} has ${f.part}`).toBe(true);
  }

  // Google files need real Google; the pretend account says so instead of failing quietly.
  // (They are checked for real accounts at API level in tests/test_new_files.py.)
  for (const [item, sub] of [
    ["Blank Google Doc", "Google Docs"],
    ["Blank Google Sheet", "Google Sheets"],
    ["Blank Google Slides", "Google Slides"],
  ]) {
    const popup = page.waitForEvent("popup");
    await newItem(page, item, sub);
    await (await popup).close().catch(() => undefined); // the app closes it itself on the error
    await expect(page.getByText(PRETEND_NATIVE_MSG).first()).toBeVisible();
  }

  // Sync a Drive folder… opens the wizard's folder step.
  await newItem(page, "Sync a Drive folder…");
  await expect(page.getByRole("heading", { name: "Choose what to sync" })).toBeVisible();

  // Outside every synced folder (a second pretend account with nothing synced yet) the items
  // that add files are turned off and say why; "Sync a Drive folder…" stays on.
  await page.goto("/drive");
  await page.getByRole("button", { name: "Account: alice@example.com" }).click();
  await page.getByRole("group", { name: "Google accounts" }).getByRole("button", { name: /bob@example\.com/ }).click();
  await expect(page.getByRole("button", { name: "Account: bob@example.com" })).toBeVisible();
  await openNew(page);
  await expect(page.getByRole("menu", { name: "New" }).getByText(NOT_SYNCED_NOTE)).toBeVisible();
  for (const name of ["New folder", "Upload files", "Upload folder"]) {
    await expect(page.getByRole("menuitem", { name, exact: true })).toBeDisabled();
  }
  await expect(page.getByRole("menuitem", { name: "Sync a Drive folder…", exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");

  expect(problems, problems.join("\n")).toEqual([]);
});
