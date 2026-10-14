import { expect, type Page, test } from "@playwright/test";

import { fakeUnsubHits, ready } from "./helpers";

// Unsubscribe (0.5.2): with the pretend Gmail of carol@example.com (two senders, 3 unread mails
// each) and the pretend unsubscribe site, unsubscribe one sender by a one-click request, get
// "Needs you" for the sender that only has an address, then (after Allow Gmail filters is on)
// Auto-archive and Keep it. Carol has her own mailbox so the sample mailbox's counts stay as
// the other tests expect.
const ONE_CLICK = "one-click@promo.example";
const MAILTO = "mailto-only@promo.example";

async function carolId(page: Page): Promise<number> {
  const accounts = (await (await page.request.get("/api/accounts")).json()) as { id: number; email: string }[];
  const found = accounts.find((a) => a.email === "carol@example.com");
  expect(found, "carol@example.com must be seeded with --unsub-mail").toBeDefined();
  return found!.id;
}

/** Mail backup set up for carol and its first backup done (6 mails). */
async function setUpCarolsMail(page: Page) {
  await page.goto("/mail");
  const status = page.getByText(/^6 mails · .* · checked/);
  const setUp = page.getByRole("button", { name: "Set up mail backup" }).first();
  await expect(status.or(setUp)).toBeVisible();
  if (await status.isVisible()) return;
  await setUp.click();
  await expect(page.getByRole("heading", { name: "Start mail backup" })).toBeVisible();
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(status).toBeVisible({ timeout: 120_000 });
}

/** Click a sender's Unsubscribe button and wait for the job window's line about it. */
async function runJob(page: Page, start: () => Promise<void>, line: string) {
  await start();
  const job = page.getByRole("dialog");
  await expect(job.getByRole("list", { name: "Results" })).toContainText(line, { timeout: 60_000 });
  return job;
}

async function rowMenu(page: Page, address: string, item: string) {
  await page.getByRole("button", { name: `More actions for ${address}` }).click();
  await page.getByRole("menuitem", { name: item, exact: true }).click();
}

test("unsubscribe: one-click, needs you, then auto-archive and keep", async ({ page }) => {
  test.setTimeout(300_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await ready(page);
  const id = await carolId(page);
  await page.addInitScript((value) => localStorage.setItem("drivesync.account", value), String(id));
  await setUpCarolsMail(page);

  // 1. Mail → Unsubscribe. The scan line goes away (new mail is saved with headers, so it may never show).
  await page.goto("/mail/unsubscribe");
  await expect(page.getByRole("heading", { name: "Unsubscribe" })).toBeVisible();
  await expect(page.getByText(/^Scanning old mail/)).toHaveCount(0);

  // 2. Both senders are suggested (3 unread mails each).
  await expect(page.getByRole("button", { name: `Unsubscribe from ${ONE_CLICK}` })).toBeVisible();
  await expect(page.getByRole("button", { name: `Unsubscribe from ${MAILTO}` })).toBeVisible();

  // 3. Unsubscribe the first: the job says so, the site got one POST, the sender moves tabs.
  const before = await fakeUnsubHits();
  let job = await runJob(page, () => page.getByRole("button", { name: `Unsubscribe from ${ONE_CLICK}` }).click(), `${ONE_CLICK}: Unsubscribed`);
  const hits = await fakeUnsubHits();
  if (hits) {
    expect(hits.length - (before?.length ?? 0)).toBe(1);
    expect(hits[hits.length - 1]).toMatchObject({ method: "POST", path: "/one-click" });
  }
  await job.getByRole("button", { name: "Close" }).click();
  await page.getByRole("tab", { name: "Unsubscribed" }).click();
  await expect(page.getByRole("button", { name: `Unsubscribe from ${ONE_CLICK}` })).toBeVisible();
  await page.getByRole("tab", { name: "Suggested" }).click();
  await expect(page.getByRole("button", { name: `Unsubscribe from ${ONE_CLICK}` })).toHaveCount(0);

  // 4. The sender with only an address, filters off: CleanSync never sends mail, so it needs you.
  job = await runJob(page, () => page.getByRole("button", { name: `Unsubscribe from ${MAILTO}` }).click(), `${MAILTO}: Needs you`);
  await expect(job.getByRole("link", { name: "Write to it" })).toBeVisible();
  await job.getByRole("button", { name: "Close" }).click();

  // 5. Settings → Accounts → Allow Gmail filters: the pretend account turns it on without Google.
  await page.goto("/settings?tab=accounts");
  const box = page.getByRole("group", { name: "Allow Gmail filters" });
  await box.getByRole("button", { name: "Turn on" }).click();
  await expect(box).toContainText("Allow Gmail filters: On");

  // 6. Auto-archive the sender: done, and it shows under the Auto-archived tab.
  await page.goto("/mail/unsubscribe");
  job = await runJob(page, () => rowMenu(page, MAILTO, "Auto-archive"), `${MAILTO}: Auto-archived`);
  await job.getByRole("button", { name: "Close" }).click();
  await page.getByRole("tab", { name: "Auto-archived" }).click();
  await expect(page.getByRole("button", { name: `Unsubscribe from ${MAILTO}` })).toBeVisible();

  // 7. Keep it: "Kept", and it shows under the Kept tab.
  job = await runJob(page, () => rowMenu(page, MAILTO, "Keep"), `${MAILTO}: Kept`);
  await job.getByRole("button", { name: "Close" }).click();
  await page.getByRole("tab", { name: "Kept" }).click();
  await expect(page.getByRole("button", { name: `Unsubscribe from ${MAILTO}` })).toBeVisible();

  // Turn Allow Gmail filters off again: later runs start from the default.
  await page.goto("/settings?tab=accounts");
  await box.getByRole("button", { name: "Turn off" }).click();
  await expect(box).toContainText("Allow Gmail filters: Off");
});
