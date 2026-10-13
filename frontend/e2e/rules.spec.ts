import { expect, type Page, test } from "@playwright/test";

import { fakeGmailDelete, fakeGmailTrash, ready, setUpMail } from "./helpers";

// Mail rules with the pretend model (backend/app/devtools/fake_model.py): it picks the rule whose
// name is in the mail's subject. CleanSync's server calls it, so the address is as the server
// sees it (in CI the model runs inside the container).
const FAKE_MODEL_URL = process.env.E2E_FAKE_MODEL_URL ?? "http://127.0.0.1:11999/v1";

/** The list rows of a view, after it has loaded. */
async function rows(page: Page, view: string) {
  await page.goto(`/mail?view=${view}`);
  const list = page.getByRole("list", { name: "Mail" });
  await expect(list.or(page.getByText("No mail here."))).toBeVisible();
  return list;
}

test("a rule written in a sentence runs from its card, applies, undoes; a search offloads, restores and deletes forever", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await ready(page);
  await setUpMail(page);

  // 1. Point Sorting at the pretend model and test it: the sample mail is a weekly digest.
  await page.goto("/settings?tab=models");
  await page.getByRole("button", { name: "Add connection" }).click();
  const connection = page.getByRole("dialog", { name: "Add connection" });
  await connection.getByLabel("Name").fill("Fake");
  await connection.getByLabel("Address").fill(FAKE_MODEL_URL);
  await connection.getByRole("button", { name: "Save connection" }).click();
  await expect(connection).toBeHidden();
  const sorting = page.getByRole("group", { name: "Sorting" });
  await sorting.getByLabel("Connection").selectOption({ label: "Fake" });
  await sorting.getByRole("button", { name: "List models" }).click();
  await expect(sorting.locator("datalist option[value='fake-sorter']")).toHaveCount(1);
  await sorting.getByLabel("Model").fill("fake-sorter");
  await sorting.getByRole("button", { name: "Save", exact: true }).click();
  await expect(sorting.getByText("Saved.")).toBeVisible();
  await sorting.getByRole("button", { name: "Test", exact: true }).click();
  await expect(sorting.getByText(/^Answered in .* · picked Newsletters · answer mode json_schema$/)).toBeVisible();

  // 2. Write a rule from a sentence (Writing rules falls back to the Sorting model) and save it.
  await page.goto("/mail/rules");
  const write = page.getByRole("region", { name: "Write a rule" });
  await write.getByLabel("Describe mail to sort, in your own words").fill("Archive invoices from shops");
  await write.getByRole("button", { name: "Write the rule" }).click();
  await expect(write.getByLabel("Which mail is this for?")).toHaveValue("Archive invoices from shops");
  await expect(write.getByRole("checkbox", { name: "Archive" })).toBeChecked();
  await write.getByLabel("Name").fill("Invoice");
  await write.getByRole("button", { name: "Save rule" }).click();
  await expect(page.getByText("Rule “Invoice” saved.")).toBeVisible();

  // 3. Run the rule from its card: it decides the Inbox with this rule alone, lists what it
  //    matched (all picked), and archives the mail on approval. No typed count for Archive.
  await page.getByRole("button", { name: "Run, Invoice" }).click();
  const run = page.getByRole("dialog", { name: "Run rules" });
  const matched = run.getByRole("list", { name: "Matched mail" });
  await expect(matched.getByRole("listitem")).toHaveCount(1, { timeout: 60_000 });
  await expect(matched).toContainText("Invoice October");
  await expect(matched).toContainText("subject mentions Invoice");
  await expect(run.getByText(/^Invoice would archive 1 mail\./)).toBeVisible();
  await expect(run.getByRole("checkbox", { name: "Pick all" })).toBeChecked();
  await run.getByRole("button", { name: "Archive 1 mail" }).click();
  await expect(run.getByText("Done. Applied 1, skipped 0, failed 0, left out 0.")).toBeVisible({ timeout: 60_000 });
  await run.getByRole("button", { name: "Close" }).click();
  await expect((await rows(page, "inbox")).getByRole("link", { name: /Invoice October/ })).toHaveCount(0); // archived

  // 4. Undo it from its chip in All mail: the mail is back in the inbox.
  await page.goto("/mail?view=all&q=invoice");
  const chip = page.getByRole("button", { name: "Invoice · applied" });
  await chip.click();
  await page.getByRole("group", { name: "Rule: Invoice" }).getByRole("button", { name: "Undo" }).click();
  await expect(chip).toBeHidden();
  await expect((await rows(page, "inbox")).getByRole("link", { name: /Invoice October/ })).toBeVisible();

  // 5. Build a search with the form in the Inbox, pick all of it, and offload it.
  await page.goto("/mail?view=inbox");
  await page.getByRole("button", { name: "Search options" }).click();
  const options = page.getByRole("region", { name: "Search options" });
  await options.getByLabel("Has the words").fill("weekend");
  await options.getByLabel("Has attachment").check();
  await options.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("textbox", { name: "Search mail" })).toHaveValue("weekend has:attachment");
  await expect(page).toHaveURL(/view=inbox/);
  const found = page.getByRole("list", { name: "Mail" });
  await expect(found.getByRole("listitem")).toHaveCount(1);
  await expect(found.getByRole("link", { name: /Photos from the weekend/ })).toBeVisible();
  await page.getByRole("checkbox", { name: "Pick all" }).check();
  const picked = page.getByRole("region", { name: "Picked mail" });
  await expect(picked.getByText("The 1 mail in this search is picked.")).toBeVisible();
  await picked.getByRole("button", { name: "Offload", exact: true }).click();
  const offload = page.getByRole("dialog", { name: "Offload mail" });
  await offload.getByRole("button", { name: "Preview" }).click();
  await offload.getByLabel("To start, type 1").fill("1");
  await offload.getByRole("button", { name: "Start offload" }).click();
  await expect(page.getByText(/^Offloaded 1 of 1 mails/)).toBeVisible({ timeout: 60_000 });
  expect(fakeGmailTrash() ?? ["Photos from the weekend"]).toContain("Photos from the weekend");

  // 6. It's in Offloaded now and gone from the Inbox; restore it from Offloaded.
  await expect((await rows(page, "inbox")).getByRole("link", { name: /Photos from the weekend/ })).toHaveCount(0);
  const offloaded = await rows(page, "offloaded");
  await offloaded.getByRole("link", { name: /Photos from the weekend/ }).click();
  await page.getByRole("region", { name: "Open mail" }).getByRole("button", { name: "Restore to Gmail" }).click();
  await expect(page.getByText("Putting 1 mail back in Gmail. Sync activity shows when it's done.")).toBeVisible();
  await expect
    .poll(async () => (await rows(page, "inbox")).getByRole("link", { name: /Photos from the weekend/ }).count(), { timeout: 60_000 })
    .toBe(1);

  // 7. Delete a mail in Gmail itself: it shows in Deleted in Gmail, and Delete forever removes CleanSync's copy.
  if (fakeGmailDelete("Old newsletter #1")) {
    await page.goto("/mail?view=inbox");
    await page.getByRole("button", { name: "Check now" }).click();
    await expect
      .poll(async () => (await rows(page, "deleted")).getByRole("link", { name: /Old newsletter #1/ }).count(), { timeout: 60_000 })
      .toBe(1);
    const search = page.getByRole("textbox", { name: "Search mail" });
    await search.fill("newsletter");
    await search.press("Enter");
    await expect(page).toHaveURL(/view=deleted/);
    await page.getByRole("checkbox", { name: "Pick all" }).check();
    const bar = page.getByRole("region", { name: "Picked mail" });
    await expect(bar.getByText("The 1 mail in this search is picked.")).toBeVisible();
    await bar.getByRole("button", { name: "Delete forever…" }).click();
    const forever = page.getByRole("dialog", { name: "Delete forever" });
    await forever.getByLabel("Type 1 to delete forever").fill("1");
    await forever.getByRole("button", { name: "Delete forever" }).click();
    await expect(page.getByText("Deleted 1 mail forever.")).toBeVisible();
    await expect(page.getByText("No mail here.")).toBeVisible();
  }

  expect(problems, problems.join("\n")).toEqual([]);
});

// Runs after the first test, which points Sorting at the pretend model and backs up the sample
// mailbox. It doesn't call setUpMail: the first test's Delete forever can change the kept count.
test("Run rules on a search leaves out an unticked mail; Run on the Marketing card archives it", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await ready(page);

  // 1. Add the starter rule Marketing.
  await page.goto("/mail/rules");
  await page.getByRole("button", { name: "Starter rules…" }).click();
  const starters = page.getByRole("dialog", { name: "Starter rules" });
  await starters.getByRole("checkbox", { name: /^Marketing/ }).check();
  await starters.getByRole("button", { name: "Add" }).click();
  await expect(page.getByText("Added 1 rule.")).toBeVisible();

  // 2. A search in the Inbox, Run rules with Marketing ticked, one mail unticked.
  await page.goto("/mail?view=inbox&q=from%3Adeals");
  await expect(page.getByRole("list", { name: "Mail" }).getByRole("listitem")).toHaveCount(2);
  await page.getByRole("button", { name: "Run rules", exact: true }).click();
  const picker = page.getByRole("dialog", { name: "Run rules" });
  await expect(picker.getByText("Run on: 2 mails matching from:deals")).toBeVisible();
  await expect(picker.getByRole("button", { name: "Run", exact: true })).toBeDisabled();
  await picker.getByRole("checkbox", { name: /^Marketing/ }).check();
  await picker.getByRole("button", { name: "Run", exact: true }).click();
  const run = page.getByRole("dialog", { name: "Run rules" });
  const matched = run.getByRole("list", { name: "Matched mail" });
  await expect(matched.getByRole("listitem")).toHaveCount(2, { timeout: 60_000 });
  await expect(run.getByText(/^Marketing would change 2 mails: Label Marketing, Archive\./)).toBeVisible();
  await run.getByRole("checkbox", { name: "Pick Spring offers just for you" }).uncheck();
  await expect(run.getByRole("checkbox", { name: "Pick all" })).not.toBeChecked();
  await run.getByRole("button", { name: "Apply 1 change" }).click();
  await expect(run.getByText("Done. Applied 1, skipped 0, failed 0, left out 1.")).toBeVisible({ timeout: 60_000 });
  await run.getByRole("button", { name: "Close" }).click();
  const inbox = await rows(page, "inbox");
  await expect(inbox.getByRole("link", { name: /Big sale/ })).toHaveCount(0);
  await expect(inbox.getByRole("link", { name: /Spring offers just for you/ })).toBeVisible(); // left alone

  // 3. Run on the Marketing card covers the Inbox: it finds the mail left out before.
  await page.goto("/mail/rules");
  await page.getByRole("button", { name: "Run, Marketing" }).click();
  const cardRun = page.getByRole("dialog", { name: "Run rules" });
  const list = cardRun.getByRole("list", { name: "Matched mail" });
  await expect(list.getByRole("listitem")).toHaveCount(1, { timeout: 60_000 });
  await expect(list).toContainText("Spring offers just for you");
  await cardRun.getByRole("button", { name: "Apply 1 change" }).click();
  await expect(cardRun.getByText("Done. Applied 1, skipped 0, failed 0, left out 0.")).toBeVisible({ timeout: 60_000 });
  await cardRun.getByRole("button", { name: "Close" }).click();

  // 4. Both marketing mails are archived: in Archived, not in the Inbox.
  const archived = await rows(page, "archived");
  await expect(archived.getByRole("link", { name: /Big sale/ })).toBeVisible();
  await expect(archived.getByRole("link", { name: /Spring offers just for you/ })).toBeVisible();
  await expect((await rows(page, "inbox")).getByRole("link", { name: /Spring offers/ })).toHaveCount(0);

  expect(problems, problems.join("\n")).toEqual([]);
});

test("a model that hits its limit pauses its connection until Resume now", async ({ page }) => {
  test.setTimeout(120_000);
  await ready(page);
  await page.goto("/settings?tab=models");
  await page.getByRole("button", { name: "Add connection" }).click();
  const dialog = page.getByRole("dialog", { name: "Add connection" });
  await dialog.getByLabel("Name").fill("Limited");
  await dialog.getByLabel("Address").fill(FAKE_MODEL_URL);
  await dialog.getByRole("button", { name: "Save connection" }).click();
  await expect(dialog).toBeHidden();

  // The model "fake-limited" always says its daily limit is used up; Test shows the server's words.
  const writing = page.getByRole("group", { name: "Writing rules" });
  await writing.getByLabel("Connection").selectOption({ label: "Limited" });
  await writing.getByLabel("Model").fill("fake-limited");
  await writing.getByRole("button", { name: "Test", exact: true }).click();
  await expect(writing.getByText(/The server said: Rate limit reached for requests per day/)).toBeVisible();

  // The connection is now paused: the row says until when, and Resume now ends the pause.
  const row = page.getByRole("listitem").filter({ hasText: "Limited" });
  await expect(row.getByText(/Paused until/)).toBeVisible();
  await row.getByRole("button", { name: "Show the server's message" }).click();
  await expect(row.getByText(/Try again tomorrow/)).toBeVisible();
  await row.getByRole("button", { name: "Resume now" }).click();
  await expect(row.getByText(/Paused until/)).toBeHidden();

  // Leave the settings as the first test expects them.
  await writing.getByLabel("Connection").selectOption({ label: "None" });
  await writing.getByRole("button", { name: "Save", exact: true }).click();
  await row.getByRole("button", { name: "Remove Limited" }).click();
  await expect(row).toBeHidden();
});
