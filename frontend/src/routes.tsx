import { useEffect, useRef } from "react";
import { Navigate, Route, Routes, useNavigate, useSearchParams } from "react-router-dom";

import { useSetup } from "./api/queries";
import { AppShell } from "./components/AppShell";
import { NewMenu } from "./components/NewMenu";
import { useToast } from "./components/Toasts";
import { UploadTray } from "./components/UploadTray";
import { AccountProvider, useCurrentAccount } from "./hooks/useAccount";
import { OAUTH_RETURN_KEY } from "./hooks/useConnect";
import { ActivityPage } from "./pages/ActivityPage";
import { ConflictsPage } from "./pages/ConflictsPage";
import { DrivePage } from "./pages/DrivePage";
import { HomePage, RecentPage, SearchPage, StarredPage } from "./pages/ListPages";
import { BlockedPage, PairDetailPage } from "./pages/PairDetailPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SetupWizard } from "./pages/SetupWizard";
import { StoragePage } from "./pages/StoragePage";

/**
 * Google's sign-in sends the browser to "/?connected=<id>" or "/?oauth_error=<text>".
 * Show what happened, then go back where the person started (saved before leaving).
 */
function OAuthReturn() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { setAccountId } = useCurrentAccount();
  const connected = params.get("connected");
  const error = params.get("oauth_error");
  const handled = useRef(false);
  useEffect(() => {
    if (!connected && !error) return;
    if (handled.current) return; // StrictMode runs effects twice; show the message once
    handled.current = true;
    let back = "/";
    try {
      back = sessionStorage.getItem(OAUTH_RETURN_KEY) || "/";
      sessionStorage.removeItem(OAUTH_RETURN_KEY);
    } catch {
      // go Home
    }
    if (!back.startsWith("/") || back.startsWith("//")) back = "/";
    if (connected) {
      setAccountId(Number(connected));
      toast("Google account connected.");
      if (back.startsWith("/setup")) back = "/setup?step=folders";
    }
    if (error) toast(error, { tone: "error" });
    navigate(back, { replace: true });
  }, [connected, error, navigate, toast, setAccountId]);
  return null;
}

/** Home sends a brand-new installation to the setup wizard. */
function HomeOrSetup() {
  const setup = useSetup();
  const [params] = useSearchParams();
  if (params.get("connected") || params.get("oauth_error")) return <OAuthReturn />;
  if (setup.data && setup.data.pairs === 0) return <Navigate to="/setup" replace />;
  return <HomePage />;
}

export function AppRoutes() {
  return (
    <AccountProvider>
      <Routes>
        <Route path="setup" element={<SetupWizard />} />
        <Route element={<AppShell newButton={<NewMenu />} />}>
          <Route index element={<HomeOrSetup />} />
          <Route path="drive" element={<DrivePage />} />
          <Route path="recent" element={<RecentPage />} />
          <Route path="starred" element={<StarredPage />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="conflicts" element={<ConflictsPage />} />
          <Route path="activity" element={<ActivityPage />} />
          <Route path="activity/:pairId" element={<PairDetailPage />} />
          <Route path="activity/:pairId/blocked" element={<BlockedPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="storage" element={<StoragePage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      <UploadTray />
    </AccountProvider>
  );
}
