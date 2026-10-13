import { expect, type Page, test } from "@playwright/test";

import { fakeGmailMails, ready } from "./helpers";

// Bulk offload spec §11: with the fake Gmail, offload a search with permanent delete on, see
// "Removed from Gmail", then restore.
// Runs after the rules test, which has already backed
// the mailbox up (so no setUpMail here: its count of kept mail no longer holds).
const SUBJECT = "Holiday plans";

async function aliceId(page: Page): Promise<number> {
  const accounts = (await (await page.request.get("/api/accounts")).json()) as { id: number; email: string }[];
  const found = accounts.find((a) => a.email === "alice@example.com");
  expect(found).toBeDefined();
  return found!.id;
}

async function csrf(page: Page): Promise<string> {
  return ((await (await page.request.get("/api/auth/me")).json()) as { csrf_token: string }).csrf_token;
}

function inGmail(subject: string): boolean | null {
  const mails = fakeGmailMails();
  return mails && mails.some((m) => m.subject === subject && !m.labels.includes("TRASH"));
}

test("permanent delete: turn it on, offload a search, the space is free at once, restore it", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await ready(page);
  const id = await aliceId(page);
  const headers = { "X-CSRF-Token": await csrf(page) };

  // An earlier test may have offloaded it: put it back in Gmail first.
  const back = await page.request.post("/api/mail/restore", { headers, data: { account_id: id, view: "kept", q: SUBJECT } });
  expect([202, 409], await back.text()).toContain(back.status());
  await expect
    .poll(
      async () => {
        const r = await page.request.get(`/api/mail/threads?account_id=${id}&view=all&q=${encodeURIComponent(SUBJECT)}`);
        return ((await r.json()) as { threads: { subject: string }[] }).threads.map((t) => t.subject);
      },
      { timeout: 60_000 },
    )
    .toContain(SUBJECT);

  // Settings → Accounts: the pretend account gets the permission without Google's sign-in.
  await page.goto("/settings?tab=accounts");
  const box = page.getByRole("group", { name: "Allow permanent delete" });
  await expect(box).toContainText("Offload moves mail to Gmail's trash.");
  await box.getByRole("button", { name: "Turn on" }).click();
  await expect(box).toContainText("Offload and Delete forever remove mail from Gmail directly. Gmail's trash isn't used.");

  // Offload the search: the preview says the space comes back at once.
  await page.goto(`/mail?q=${encodeURIComponent(SUBJECT)}`);
  await page.getByRole("button", { name: "Offload mail…" }).click();
  const dialog = page.getByRole("dialog", { name: "Offload mail" });
  await expect(dialog).toContainText("Offloaded mail is removed from Gmail for good and stays readable here.");
  await dialog.getByRole("button", { name: "Preview" }).click();
  const line = dialog.getByText(/^\d+ mails?, .* The space comes back in Gmail at once\.$/);
  await expect(line).toBeVisible();
  const count = Number(/^(\d+)/.exec((await line.textContent()) ?? "")?.[1]);
  expect(count).toBeGreaterThan(0);
  await dialog.getByLabel(`To start, type ${count}`).fill(String(count));
  await dialog.getByRole("button", { name: "Start offload" }).click();

  // The offload's page: done, removed from Gmail, no trash line.
  await expect(page.getByRole("heading", { name: "Mail offload" })).toBeVisible();
  await expect(page.getByText("Done", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Removed from Gmail. The space is free now.")).toBeVisible();
  await expect(page.getByText(/In Gmail's trash until about/)).toHaveCount(0);
  const gone = fakeGmailMails();
  if (gone) expect(gone.map((m) => m.subject)).not.toContain(SUBJECT);

  // Restore: Gmail no longer has it, so it is imported again from the copy here.
  await page.getByRole("button", { name: "Restore all to Gmail" }).click();
  await expect(page.getByText(/^Putting \d+ mails? back in Gmail\./)).toBeVisible();
  if (inGmail(SUBJECT) !== null) await expect.poll(() => inGmail(SUBJECT), { timeout: 60_000 }).toBe(true);
  await expect
    .poll(
      async () => {
        const r = await page.request.get(`/api/mail/threads?account_id=${id}&view=all&q=${encodeURIComponent(SUBJECT)}`);
        return ((await r.json()) as { threads: { subject: string }[] }).threads.map((t) => t.subject);
      },
      { timeout: 60_000 },
    )
    .toContain(SUBJECT);

  // Turn it off again: later runs start from the default.
  await page.goto("/settings?tab=accounts");
  await box.getByRole("button", { name: "Turn off" }).click();
  await expect(box).toContainText("Offload moves mail to Gmail's trash.");
});
