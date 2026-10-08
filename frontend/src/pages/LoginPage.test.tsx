import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { getCsrfToken } from "../api/client";
import { RequireAuth } from "../components/AuthGate";
import { json, mockApi, renderApp } from "../test/utils";
import { ChangePasswordPage } from "./ChangePasswordPage";
import { LoginPage } from "./LoginPage";

const HEALTH = { "GET /api/health": { status: "ok", version: "0.1.0", app_name: "DriveSync" } };

describe("login", () => {
  it("logs in and goes to the forced password change", async () => {
    const { calls } = mockApi({
      ...HEALTH,
      "POST /api/auth/login": { username: "admin", must_change_password: true, csrf_token: "fresh" },
    });
    renderApp(<LoginPage />, { route: "/login", path: "/login" });
    await userEvent.type(screen.getByLabelText("Password"), "initial-pass-123");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/change-password"));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ username: "admin", password: "initial-pass-123" });
    expect(getCsrfToken()).toBe("fresh");
  });

  it("explains a wrong password", async () => {
    mockApi({ ...HEALTH, "POST /api/auth/login": () => json(401, { detail: "invalid_credentials" }) });
    renderApp(<LoginPage />, { route: "/login", path: "/login" });
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong username or password.");
  });

  it("shows the configured name", async () => {
    mockApi({ "GET /api/health": { status: "ok", version: "0.1.0", app_name: "HomeDrive" } });
    renderApp(<LoginPage />, { route: "/login", path: "/login" });
    expect(await screen.findByText("HomeDrive")).toBeInTheDocument();
  });
});

describe("password change", () => {
  it("checks the new password, saves it and goes on", async () => {
    const { calls } = mockApi({
      ...HEALTH,
      "GET /api/auth/me": { username: "admin", must_change_password: true, csrf_token: "a" },
      "POST /api/auth/change-password": { username: "admin", must_change_password: false, csrf_token: "b" },
    });
    renderApp(<ChangePasswordPage />, { route: "/change-password", path: "/change-password" });
    await userEvent.type(screen.getByLabelText("Current password"), "initial-pass-123");
    await userEvent.type(screen.getByLabelText(/^New password(?! again)/), "short");
    expect(screen.getByText("Use at least 12 characters.")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText(/^New password(?! again)/));
    await userEvent.type(screen.getByLabelText(/^New password(?! again)/), "a much better passphrase");
    await userEvent.type(screen.getByLabelText("New password again"), "a much better passphrase");
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    await waitFor(() => expect(getCsrfToken()).toBe("b"));
    expect(calls.find((c) => c.path === "/api/auth/change-password")?.body).toEqual({
      current_password: "initial-pass-123",
      new_password: "a much better passphrase",
    });
  });
});

describe("RequireAuth", () => {
  const tree = (
    <Routes>
      <Route element={<RequireAuth />}>
        <Route path="/drive" element={<p>files</p>} />
      </Route>
    </Routes>
  );

  it("sends logged-out people to the login page", async () => {
    mockApi({ "GET /api/auth/me": () => json(401, { detail: "not_logged_in" }) });
    renderApp(tree, { route: "/drive" });
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/login"));
  });

  it("holds everything back until the starting password is changed", async () => {
    mockApi({ "GET /api/auth/me": { username: "admin", must_change_password: true, csrf_token: "x" } });
    renderApp(tree, { route: "/drive" });
    await waitFor(() => expect(screen.getAllByTestId("location")[0]).toHaveTextContent("/change-password"));
  });

  it("lets a normal session through", async () => {
    mockApi({ "GET /api/auth/me": { username: "admin", must_change_password: false, csrf_token: "x" } });
    renderApp(tree, { route: "/drive" });
    expect(await screen.findByText("files")).toBeInTheDocument();
  });
});
