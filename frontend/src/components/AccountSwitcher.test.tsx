import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Avatar } from "./AccountSwitcher";

describe("Avatar", () => {
  it("shows the photo when there is one, and the initial when it fails", () => {
    const { container } = render(<Avatar email="ann@example.com" accountId={3} hasPhoto />);
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("/api/accounts/3/photo");
    fireEvent.error(img);
    expect(screen.getByText("a")).toBeInTheDocument();
  });

  it("shows the initial without a photo", () => {
    render(<Avatar email="bob@example.com" />);
    expect(screen.getByText("b")).toBeInTheDocument();
  });
});
