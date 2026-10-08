import { Route, Routes } from "react-router-dom";

import { FullPage } from "./components/AuthGate";
import { AccountProvider } from "./hooks/useAccount";

// Stand-in until Task 18 replaces this file with the full route table.
export function AppRoutes() {
  return (
    <AccountProvider>
      <Routes>
        <Route path="*" element={<FullPage>You are logged in.</FullPage>} />
      </Routes>
    </AccountProvider>
  );
}
