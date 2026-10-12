import { expect, type Page, test } from "@playwright/test";

import { fakeGmailTrash, ready, setUpMail } from "./helpers";

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

test("a rule written in a sentence previews, applies, undoes; Delete keeps a copy until Delete forever", async ({ page }) => {
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

  // 3. Run the rules on the inbox: a preview that changes nothing, then the typed count.
  await page.getByRole("button", { name: "Run rules on…" }).click();
  const run = page.getByRole("dialog", { name: "Run rules" });
  const typeIt = run.getByLabel("Type 1 to make these changes");
  await expect(typeIt).toBeVisible({ timeout: 60_000 });
  const examples = run.getByRole("list", { name: "Examples for Invoice" });
  await expect(examples).toContainText("Invoice October");
  await expect(examples).toContainText("subject mentions Invoice");
  await typeIt.fill("1");
  await run.getByRole("button", { name: "Change 1 mail" }).click();
  await expect(run.getByText("Done. Applied 1, skipped 0, failed 0.")).toBeVisible({ timeout: 60_000 });
  await run.getByRole("button", { name: "Close" }).click();
  await expect((await rows(page, "inbox")).getByRole("link", { name: /Invoice October/ })).toHaveCount(0); // archived

  // 4. Undo it from its chip in All mail: the mail is back in the inbox.
  await page.goto("/mail?view=all&q=invoice");
  const chip = page.getByRole("button", { name: "Invoice · applied" });
  await chip.click();
  await page.getByRole("group", { name: "Rule: Invoice" }).getByRole("button", { name: "Undo" }).click();
  await expect(chip).toBeHidden();
  await expect((await rows(page, "inbox")).getByRole("link", { name: /Invoice October/ })).toBeVisible();

  // 5. Delete a mail: it goes to Gmail's trash, and CleanSync keeps it in the Local archive.
  const inbox = await rows(page, "inbox");
  await page.getByRole("checkbox", { name: "Pick Photos from the weekend" }).check();
  await page.getByRole("region", { name: "Picked mail" }).getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("dialog", { name: "Delete", exact: true }).getByRole("button", { name: "Move to trash" }).click();
  await expect(page.getByText("Moved 1 mail to Gmail's trash.")).toBeVisible();
  await expect(inbox.getByRole("link", { name: /Photos from the weekend/ })).toHaveCount(0);
  const archive = await rows(page, "archive");
  await expect(archive.getByRole("listitem")).toHaveCount(1);
  await expect(archive.getByRole("link", { name: /Photos from the weekend/ })).toBeVisible();
  expect(fakeGmailTrash() ?? ["Photos from the weekend"]).toContain("Photos from the weekend");

  // 6. Delete it forever from the Local archive: CleanSync's copy goes; Gmail's trash is untouched.
  await page.getByRole("checkbox", { name: "Pick Photos from the weekend" }).check();
  await page.getByRole("region", { name: "Picked mail" }).getByRole("button", { name: "Delete forever…" }).click();
  const forever = page.getByRole("dialog", { name: "Delete forever" });
  await forever.getByLabel("Type 1 to delete forever").fill("1");
  await forever.getByRole("button", { name: "Delete forever" }).click();
  await expect(page.getByText("Deleted 1 mail forever.")).toBeVisible();
  await rows(page, "archive");
  await expect(page.getByText("No mail here.")).toBeVisible();
  expect(fakeGmailTrash() ?? ["Photos from the weekend"]).toContain("Photos from the weekend");

  expect(problems, problems.join("\n")).toEqual([]);
});
