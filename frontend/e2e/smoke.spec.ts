import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { choosePassword, fakeGmailTrash, firstSync, logIn, ready, setUpMail, START_PASSWORD } from "./helpers";

// The tests share one container, so they run one after another. Each one can also run on its
// own: ready() does the first-run steps (new password, first sync) when they haven't happened.
test.describe.configure({ mode: "serial" });

const PRETEND_NATIVE_MSG = "Google Docs, Sheets and Slides need a real Google account.";
const NOT_SYNCED_NOTE = "This folder isn't synced with Google Drive. Open a synced folder to add files.";
const IN_OFFLOADED_NOTE =
  "This folder is offloaded: it's kept only on this server and doesn't sync with Google Drive. Restore it to Drive to add files here.";

async function accountId(page: Page, email: string): Promise<number> {
  const accounts = (await (await page.request.get("/api/accounts")).json()) as { id: number; email: string }[];
  const found = accounts.find((a) => a.email === email);
  expect(found, email).toBeDefined();
  return found!.id;
}

/** The CSRF token the API asks for on every change. */
async function csrf(page: Page): Promise<string> {
  return ((await (await page.request.get("/api/auth/me")).json()) as { csrf_token: string }).csrf_token;
}

/** The rail (desktop width): the section links. */
function rail(page: Page) {
  return page.getByRole("navigation", { name: "Sections", exact: true });
}

/** Upload one file the way the + New menu would (that menu is hidden in this build). */
async function uploadViaApi(page: Page, accountId: number, name: string, body: string) {
  const r = await page.request.post(`/api/files/upload?account_id=${accountId}&path=&name=${encodeURIComponent(name)}`, {
    headers: { "X-CSRF-Token": await csrf(page), "Content-Type": "application/octet-stream" },
    data: Buffer.from(body),
  });
  expect(r.status(), await r.text()).toBe(200);
}

const SHOW_NEW = process.env.E2E_SHOW_NEW === "1";

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
  await choosePassword(page);

  // A new installation lands in the wizard; the seeded account skips the Google step.
  await firstSync(page);

  // The files from the pretend Drive are here and marked synced.
  await page.getByRole("button", { name: "Open My Drive" }).click();
  const welcome = page.getByRole("button", { name: "Welcome.txt", exact: true });
  await expect(welcome).toBeVisible();
  await expect(welcome.getByRole("img", { name: "Synced" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Photos", exact: true }).dblclick();
  await expect(page.locator('img[src^="/api/thumbs/"]')).toBeVisible();

  // Upload a file; the app queues a sync and the badge turns to synced.
  await uploadViaApi(page, await accountId(page, "alice@example.com"), "from-browser.txt", "uploaded by the smoke test");
  await page.goto("/drive");

  const uploaded = page.getByRole("button", { name: "from-browser.txt", exact: true });
  await expect(uploaded).toBeVisible();
  await expect(uploaded.getByRole("img", { name: "Synced" })).toBeVisible({ timeout: 90_000 });

  // Opening a text file shows it in a sandboxed tab.
  const [tab] = await Promise.all([page.waitForEvent("popup"), uploaded.dblclick()]);
  await expect(tab.locator("body")).toContainText("uploaded by the smoke test");

  expect(problems, problems.join("\n")).toEqual([]);
});

test("every + New item works in the built image", async ({ page }) => {
  test.skip(!SHOW_NEW, "New is hidden in this build (E2E_SHOW_NEW=1 with an image built with VITE_SHOW_NEW=1)");
  test.setTimeout(420_000);
  const problems: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to/i.test(m.text())) problems.push(m.text());
  });
  page.on("pageerror", (e) => problems.push(e.message));

  await ready(page);
  const aliceId = await accountId(page, "alice@example.com");

  // Make everything first, then wait for the syncs once: the changes go to Drive together.
  await newItem(page, "New folder");
  await page.getByLabel("Folder name").fill("Made by e2e");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("button", { name: "Made by e2e", exact: true })).toBeVisible();

  await page.getByLabel("Files to upload").setInputFiles({
    name: "e2e-upload.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("from the New menu test"),
  });
  await expect(page.getByRole("button", { name: "e2e-upload.txt", exact: true })).toBeVisible();

  // Upload folder: a real folder on disk, picked like a person would.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ds-e2e-"));
  const trip = path.join(tmp, "Trip");
  fs.mkdirSync(path.join(trip, "day 1"), { recursive: true });
  fs.writeFileSync(path.join(trip, "day 1", "notes.txt"), "a folder upload");
  await page.getByLabel("Folder to upload").setInputFiles(trip);
  await expect(page.getByRole("button", { name: "Trip", exact: true })).toBeVisible();

  // Word, Excel and PowerPoint files from the built-in templates.
  const office = [
    { item: "Word file (.docx)", sub: "Google Docs", name: "Untitled document.docx", part: "word/document.xml" },
    { item: "Excel file (.xlsx)", sub: "Google Sheets", name: "Untitled spreadsheet.xlsx", part: "xl/workbook.xml" },
    { item: "PowerPoint file (.pptx)", sub: "Google Slides", name: "Untitled presentation.pptx", part: "ppt/presentation.xml" },
  ];
  for (const f of office) {
    await newItem(page, f.item, f.sub);
    await expect(page.getByRole("button", { name: f.name, exact: true })).toBeVisible();
  }

  // All of them reach Drive.
  for (const name of ["Made by e2e", "e2e-upload.txt", "Trip", ...office.map((f) => f.name)]) {
    await expectSynced(page, name);
  }
  // Real Office files (zip packages with the right main part) that download.
  for (const f of office) {
    const bytes = await download(page, aliceId, f.name);
    expect(bytes.subarray(0, 2).toString(), `${f.name} is a zip package`).toBe("PK");
    expect(bytes.includes(Buffer.from("[Content_Types].xml")), `${f.name} has content types`).toBe(true);
    expect(bytes.includes(Buffer.from(f.part)), `${f.name} has ${f.part}`).toBe(true);
  }
  await page.getByRole("button", { name: "Trip", exact: true }).dblclick();
  await expectSynced(page, "day 1");
  await page.getByRole("button", { name: "day 1", exact: true }).dblclick();
  await expectSynced(page, "notes.txt");
  expect((await download(page, aliceId, "Trip/day 1/notes.txt")).toString()).toBe("a folder upload");
  await page.goto("/drive");
  await expect(page.getByRole("button", { name: "Welcome.txt", exact: true })).toBeVisible();

  // Google files need real Google; the pretend account says so instead of failing quietly.
  // Each item's own request is checked, not a message left over from the one before.
  // (They are checked for real accounts at API level in tests/test_new_files.py.)
  for (const [item, sub] of [
    ["Blank Google Doc", "Google Docs"],
    ["Blank Google Sheet", "Google Sheets"],
    ["Blank Google Slides", "Google Slides"],
  ]) {
    const answer = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/files/new");
    await newItem(page, item, sub);
    const response = await answer;
    expect(response.status(), item).toBe(409);
    expect(((await response.json()) as { detail: string }).detail, item).toBe(PRETEND_NATIVE_MSG);
    expect(response.request().postDataJSON().kind, item).toBe({ "Blank Google Doc": "document", "Blank Google Sheet": "spreadsheet", "Blank Google Slides": "presentation" }[item]);
  }
  await expect(page.getByText(PRETEND_NATIVE_MSG).first()).toBeVisible();

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

test("inside an offloaded folder the + New items are off and say why", async ({ page }) => {
  test.skip(!SHOW_NEW, "New is hidden in this build (E2E_SHOW_NEW=1 with an image built with VITE_SHOW_NEW=1)");
  test.setTimeout(300_000);
  await ready(page);
  const aliceId = await accountId(page, "alice@example.com");
  const headers = { "X-CSRF-Token": await csrf(page) };
  const folder = "Kept here";

  // A folder with a file, synced to the pretend Drive, then offloaded.
  const put = await page.request.post(
    `/api/files/upload?account_id=${aliceId}&path=&name=${encodeURIComponent(`${folder}/kept.txt`)}`,
    { headers: { ...headers, "Content-Type": "application/octet-stream" }, data: Buffer.from("kept on this server") },
  );
  expect(put.status(), await put.text()).toBe(200);
  // Offload checks the copy here against Drive's own listing, so it must be on Drive first.
  await page.goto("/drive");
  await expectSynced(page, folder);
  const preview = await page.request.post("/api/offloads/preview", { headers, data: { account_id: aliceId, paths: [folder] } });
  expect(((await preview.json()) as { items: { blocked: string | null }[] }).items[0].blocked).toBeNull();
  const started = await page.request.post("/api/offloads", { headers, data: { account_id: aliceId, paths: [folder] } });
  expect(started.status(), await started.text()).toBe(202);
  const batchId = ((await started.json()) as { id: string }).id;
  await expect
    .poll(
      async () => {
        const b = (await (await page.request.get(`/api/offloads/batches/${batchId}`)).json()) as { done: boolean; items: { state: string; message: string | null }[] };
        return b.done ? `${b.items[0].state}: ${b.items[0].message}` : "running";
      },
      { timeout: 60_000 },
    )
    .toMatch(/^done: /);

  await page.goto(`/drive?path=${encodeURIComponent(folder)}`);
  await expect(page.getByRole("button", { name: "kept.txt", exact: true })).toBeVisible();
  await openNew(page);
  const menu = page.getByRole("menu", { name: "New" });
  await expect(menu.getByText(IN_OFFLOADED_NOTE)).toBeVisible();
  for (const name of ["New folder", "Upload files", "Upload folder"]) {
    await expect(page.getByRole("menuitem", { name, exact: true })).toBeDisabled();
  }
  for (const [sub, items] of [
    ["Google Docs", ["Blank Google Doc", "Word file (.docx)"]],
    ["Google Sheets", ["Blank Google Sheet", "Excel file (.xlsx)"]],
    ["Google Slides", ["Blank Google Slides", "PowerPoint file (.pptx)"]],
  ] as const) {
    await page.getByRole("menuitem", { name: sub }).click();
    for (const name of items) await expect(page.getByRole("menuitem", { name, exact: true })).toBeDisabled();
  }
  await expect(page.getByRole("menuitem", { name: "Sync a Drive folder…", exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
});

test("the rail opens each section, New is hidden, and Mail asks to be set up", async ({ page }) => {
  await ready(page);
  if (!SHOW_NEW) await expect(page.getByRole("button", { name: "New", exact: true })).toHaveCount(0);
  await expect(page).toHaveTitle("CleanSync");
  await expect(rail(page).getByRole("link", { name: /^Files/ })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("navigation", { name: "Files views" }).getByRole("link", { name: /^My Drive/ })).toBeVisible();

  await rail(page).getByRole("link", { name: /^Mail/ }).click();
  await expect(page).toHaveURL(/\/mail$/);
  await expect(page.getByRole("heading", { name: "Set up mail backup" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Set up Mail", exact: true })).toBeVisible(); // the panel's card

  await rail(page).getByRole("link", { name: /^Activity/ }).click();
  await expect(page.getByRole("heading", { name: "Sync activity" })).toBeVisible();
  await rail(page).getByRole("link", { name: /^Recovery/ }).click();
  await expect(page.getByRole("navigation", { name: "Recovery views" })).toBeVisible();
  await rail(page).getByRole("link", { name: /^Settings/ }).click();
  await expect(page.getByRole("navigation", { name: "Settings views" })).toBeVisible();

  // Phone width: a bottom bar instead of the rail, and the views in a sheet.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(rail(page)).toBeHidden();
  const bar = page.getByRole("navigation", { name: "Sections at the bottom" });
  await bar.getByRole("link", { name: /^Files/ }).click();
  await page.getByRole("button", { name: "Show views" }).click();
  await page.getByRole("dialog", { name: "Views" }).getByRole("link", { name: /^Recent/ }).click();
  await expect(page).toHaveURL(/\/recent$/);
  await page.setViewportSize({ width: 1280, height: 720 });
});

test("mail backup of the pretend account, with its job on Sync activity", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await ready(page);
  await setUpMail(page);
  await expect(page.getByRole("heading", { name: "Mail backup" })).toBeVisible();
  const logs = page.getByRole("link", { name: "Log of Mail: alice@example.com" });
  await rail(page).getByRole("link", { name: /^Activity/ }).click();
  await expect(page.getByRole("region", { name: "Mail: alice@example.com" })).toBeVisible();
  await expect(logs.first()).toBeVisible();
  const before = await logs.count();

  await rail(page).getByRole("link", { name: /^Mail/ }).click();
  await page.getByRole("button", { name: "Check now" }).click();
  await expect(page.getByText(/^24 mails · .* · checked/)).toBeVisible({ timeout: 60_000 });

  // The click made a new job: one more log than before.
  await rail(page).getByRole("link", { name: /^Activity/ }).click();
  await expect(logs).toHaveCount(before + 1, { timeout: 60_000 });
  // The list is newest first, so the last link is the oldest mail job: the first backup, whose log has "saved ".
  const log = logs.last();
  const r = await page.request.get((await log.getAttribute("href"))!);
  expect(await r.text()).toContain("saved ");

  await rail(page).getByRole("link", { name: /^Settings/ }).click();
  await page.getByRole("navigation", { name: "Settings views" }).getByRole("link", { name: "Google API" }).click();
  await expect(page.getByText(/units used today \(UTC\)/)).toBeVisible();
  expect(problems, problems.join("\n")).toEqual([]);
});

async function openMailNamed(page: Page, subject: string) {
  await page.getByRole("list", { name: "Mail" }).getByRole("link", { name: new RegExp(subject.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).first().click();
  return page.frameLocator(`iframe[title="Mail: ${subject}"]`);
}

test("read mail safely: threads, a hostile mail, images on request, attachments, search, labels", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const problems: string[] = [];
  // The browser reports a request even when the page's policy stops it, so tell them apart:
  // a blocked one fails with a "csp" error; one that was allowed goes out to the network.
  const remoteImageRequests: string[] = [];
  const blockedByPolicy: string[] = [];
  const allowedImages: string[] = []; // answered by the network (or failed for a reason other than the policy)
  const bodyPolicies: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  page.on("dialog", (d) => {
    problems.push(`a mail ran script: ${d.type()} "${d.message()}"`);
    void d.dismiss();
  });
  page.on("response", (r) => {
    if (/\/api\/mail\/messages\/\d+\/body/.test(r.url())) bodyPolicies.push(r.headers()["content-security-policy"] ?? "");
  });
  page.on("requestfinished", (r) => {
    if (new URL(r.url()).hostname === "images.example.com") allowedImages.push(r.url());
  });
  page.on("request", (r) => {
    if (new URL(r.url()).hostname === "images.example.com") remoteImageRequests.push(r.url());
  });
  page.on("requestfailed", (r) => {
    if (new URL(r.url()).hostname !== "images.example.com") return;
    if (/csp/i.test(r.failure()?.errorText ?? "")) blockedByPolicy.push(r.url());
    else allowedImages.push(r.url()); // e.g. the pretend host doesn't exist: it got past the policy and failed on the network
  });
  await ready(page);
  await setUpMail(page);

  // All mail lists the 24 kept mails as threads (two of them are two-mail threads).
  await expect(page.getByText(/^24 mails · /).first()).toBeVisible();
  await expect(page.getByRole("list", { name: "Mail" }).getByRole("listitem")).toHaveCount(22);

  // A mail with a script and an onclick: the text shows, nothing runs.
  const hostile = await openMailNamed(page, "Weekly digest #52");
  await expect(hostile.getByText("News")).toBeVisible();
  expect(await hostile.locator("script").count()).toBe(0);
  expect(await hostile.locator("[onclick]").count()).toBe(0);
  await hostile.getByText("More").click(); // had an onclick in the mail; nothing runs (a dialog or page error would land in problems)
  expect(bodyPolicies.at(-1)).toContain("default-src 'none'");
  // The frame is sandboxed with no scripts and no access to CleanSync itself.
  const sandbox = (await page.locator('iframe[title="Mail: Weekly digest #52"]').getAttribute("sandbox")) ?? "";
  expect(sandbox).not.toContain("allow-scripts");
  expect(sandbox).not.toContain("allow-same-origin");

  // A tracking image waits for "Show images".
  await openMailNamed(page, "Your order has shipped");
  await expect(page.getByText("Images from the internet are hidden, so the sender can't tell you opened this mail.")).toBeVisible();
  // The sender's server is asked for the image only after the click: first the frame's policy
  // must have stopped at least one attempt, and nothing at all may have got past it.
  await expect.poll(() => blockedByPolicy.length).toBeGreaterThan(0);
  expect(allowedImages).toEqual([]);
  expect(remoteImageRequests.length).toBe(blockedByPolicy.length);
  const attemptsBefore = remoteImageRequests.length;
  await page.getByRole("button", { name: "Show images" }).click();
  await expect(page.locator('iframe[title="Mail: Your order has shipped"]')).toHaveAttribute("src", /images=1$/);
  // Now there is a new request, and the policy lets it out.
  await expect.poll(() => allowedImages.length).toBeGreaterThan(0);
  expect(remoteImageRequests.length).toBeGreaterThan(attemptsBefore);
  expect(bodyPolicies.at(-1)).toContain("img-src data: https: http:");

  // An inline image comes from the mail itself.
  const photos = await openMailNamed(page, "Trip photos");
  await expect(photos.locator('img[src^="data:image/png"]')).toHaveCount(1);

  // Attachments: preview a PDF in a new tab, download it.
  await openMailNamed(page, "Invoice October");
  const files = page.getByRole("list", { name: "Attachments" });
  // The preview opens in a new tab (headless Chromium has no PDF viewer, so the tab itself stays
  // empty); what that tab is sent is checked straight from the link.
  const preview = files.getByRole("link", { name: "Preview invoice.pdf" });
  const [pdf] = await Promise.all([page.waitForEvent("popup"), preview.click()]);
  await pdf.close();
  const shown = await page.request.get((await preview.getAttribute("href"))!);
  expect(shown.status()).toBe(200);
  expect(shown.headers()["content-type"]).toBe("application/pdf");
  const [download] = await Promise.all([page.waitForEvent("download"), files.getByRole("link", { name: "Download invoice.pdf" }).click()]);
  expect(download.suggestedFilename()).toBe("invoice.pdf");

  // Search with operators, from the search box at the top.
  const search = page.getByRole("textbox", { name: "Search mail" });
  await search.fill("from:bank");
  await search.press("Enter");
  await expect(page.getByRole("heading", { name: "Search results" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Mail" }).getByRole("listitem")).toHaveCount(2);
  await search.fill("larger:5M");
  await search.press("Enter");
  await expect(page.getByRole("list", { name: "Mail" }).getByRole("link", { name: /Big video/ })).toBeVisible();
  await search.fill("larger:abc");
  await search.press("Enter");
  await expect(page.getByText('"abc" is not a size. Use a number with K, M or G, like 5M.')).toBeVisible();

  // A label from the panel.
  const views = page.getByRole("navigation", { name: "Mail views" });
  await views.getByRole("list", { name: "Labels" }).getByRole("link", { name: /^Receipts/ }).click();
  await expect(page.getByRole("heading", { name: "Receipts" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Mail" }).getByRole("listitem")).toHaveCount(4);
  expect(problems, problems.join("\n")).toEqual([]);
});

test("on a phone, opening a mail hides the list and Back returns to it", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await ready(page);
  await setUpMail(page);
  const list = page.getByRole("list", { name: "Mail" });
  await expect(list).toBeVisible();
  await openMailNamed(page, "Trip photos");
  await expect(list).toBeHidden();
  await page.getByRole("link", { name: "Back" }).first().click();
  await expect(list).toBeVisible();
});

test("offload old mail by filter, restore one, and a filter that finds only recent mail offloads nothing", async ({
  page,
}) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await ready(page);
  await setUpMail(page);
  expect(fakeGmailTrash() ?? []).toEqual([]);

  // Build the filter: older than 1 year. The preview asks for the count.
  await page.getByRole("button", { name: "Offload mail…" }).click();
  const dialog = page.getByRole("dialog", { name: "Offload mail" });
  await dialog.getByLabel("Older than").fill("1");
  await dialog.getByLabel("Unit").selectOption("y");
  await dialog.getByRole("button", { name: "Preview" }).click();
  await expect(
    dialog.getByText(
      /^13 mails, .* The space is freed in Gmail once Gmail's trash is emptied\.$/,
    ),
  ).toBeVisible();
  await expect(
    dialog.getByRole("list", { name: "Oldest" }).getByRole("listitem").first(),
  ).toContainText("Hello");
  await dialog.getByLabel("To start, type 13").fill("13");
  await dialog.getByRole("button", { name: "Start offload" }).click();

  // The offload's page: done, nothing skipped, in Gmail's trash for about 30 days (no button to empty it).
  await expect(
    page.getByRole("heading", { name: "Mail offload" }),
  ).toBeVisible();
  await expect(page.getByText("Done", { exact: true })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByText(/^Offloaded 13 of 13 mails/)).toBeVisible();
  await expect(page.getByText(/In Gmail's trash until about/)).toBeVisible();
  await expect(
    page.getByText("Gmail empties its trash on its own after 30 days", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Empty from Gmail trash/ }),
  ).toHaveCount(0);
  const trashed = fakeGmailTrash();
  if (trashed) {
    expect(trashed).toHaveLength(13);
    expect(trashed).toContain("Holiday plans");
  }

  // Offloaded mail stays readable here (one row per thread: the Contract draft pair is one).
  await page.goto("/mail?view=offloaded");
  await expect(
    page.getByRole("list", { name: "Mail" }).getByRole("listitem"),
  ).toHaveCount(12);
  const frame = await openMailNamed(page, "Holiday plans");
  await expect(frame.locator("body")).not.toBeEmpty();

  // Restore one: it comes out of Gmail's trash and leaves the Offloaded view.
  await page.getByRole("button", { name: "Restore to Gmail" }).click();
  await expect(
    page.getByText(
      "Putting 1 mail back in Gmail. Sync activity shows when it's done.",
    ),
  ).toBeVisible();
  await expect
    .poll(
      async () => {
        await page.goto("/mail?view=offloaded");
        const rows = page
          .getByRole("list", { name: "Mail" })
          .getByRole("listitem");
        await rows.first().waitFor(); // the list has loaded
        return rows.count();
      },
      { timeout: 60_000 },
    )
    .toBe(11);
  const after = fakeGmailTrash();
  if (after) {
    expect(after).toHaveLength(12);
    expect(after).not.toContain("Holiday plans");
  }

  // A filter that only finds recent mail offloads nothing, and says why.
  await page.goto("/mail");
  await page.getByRole("button", { name: "Offload mail…" }).click();
  const again = page.getByRole("dialog", { name: "Offload mail" });
  await again.getByLabel("Filter").fill("newer_than:7d");
  await again.getByRole("button", { name: "Preview" }).click();
  await expect(again.getByRole("list", { name: "Left out" })).toContainText(
    "30 days",
  );
  await expect(
    again.getByRole("button", { name: "Start offload" }),
  ).toHaveCount(0);
});
