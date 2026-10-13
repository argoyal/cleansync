import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { mockApi, renderApp } from "../../test/utils";
import { ApproveForeverDialog } from "./ApproveForeverDialog";

describe("approving Delete forever suggestions", () => {
  it("asks for the count before approving Delete forever suggestions", async () => {
    const { calls } = mockApi({ "POST /api/rule-decisions/approve": { applied: 5 } });
    const onDone = vi.fn();
    renderApp(<ApproveForeverDialog accountId={1} ids={[1, 2, 3, 4, 5]} forever={3} onClose={() => {}} onDone={onDone} />);
    const dialog = screen.getByRole("dialog", { name: "Approve" });
    expect(dialog).toHaveTextContent("3 mails of these will be deleted forever: from Gmail and from CleanSync. This can't be undone.");
    const go = within(dialog).getByRole("button", { name: "Approve" });
    expect(go).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Type 3 to delete them forever"), "3");
    await userEvent.click(go);
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/rule-decisions/approve")?.body).toEqual({ account_id: 1, ids: [1, 2, 3, 4, 5], confirm: 3 }),
    );
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });
});
