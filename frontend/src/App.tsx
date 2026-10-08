import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AuthEvents, RequireAuth } from "./components/AuthGate";
import { ToastProvider } from "./components/Toasts";
import { ChangePasswordPage } from "./pages/ChangePasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { AppRoutes } from "./routes";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        // 4xx answers won't change by asking again.
        retry: (count, error) => count < 2 && !(error instanceof Error && "status" in error && Number(error.status) < 500),
      },
    },
  });
}

export function App({ client = makeQueryClient() }: { client?: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <BrowserRouter>
          <AuthEvents />
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<RequireAuth passwordPage />}>
              <Route path="/change-password" element={<ChangePasswordPage />} />
            </Route>
            <Route element={<RequireAuth />}>
              <Route path="/*" element={<AppRoutes />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
