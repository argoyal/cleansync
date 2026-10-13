import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ACCOUNT, mockApi, renderApp } from "../../test/utils";
import { OffloadOneDialog } from "./MailActions";

describe("Offload on one mail", () => {
  it("says the mail goes to Gmail's trash", () => {
    mockApi({});
    renderApp(<OffloadOneDialog account={ACCOUNT} messageId={1} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Offload this mail?" });
    expect(dialog).toHaveTextContent("moves the mail to Gmail's trash");
  });

  it("says the mail is deleted from Gmail for good when the mailbox allows it", () => {
    mockApi({});
    renderApp(<OffloadOneDialog account={{ ...ACCOUNT, permanent_delete: true }} messageId={1} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Offload this mail?" });
    expect(dialog).toHaveTextContent("deletes the mail from Gmail for good");
    expect(dialog).not.toHaveTextContent("trash");
  });
});
