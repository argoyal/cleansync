import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";

import { ApiError, AUTH_EVENT, type AuthSignal, clearCsrfToken, friendlyError, setCsrfToken } from "../api/client";
import { useMe } from "../api/queries";
import { Button } from "./ui";

/** Sends the person to the login or password page whenever the API says so. */
export function AuthEvents() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const location = useLocation();
  const here = location.pathname + location.search;
  useEffect(() => {
    const onSignal = (e: Event) => {
      const kind = (e as CustomEvent<AuthSignal>).detail;
      if (kind === "login") {
        clearCsrfToken();
        if (here === "/login") return; // already there; keep the page's own state
        qc.clear();
        navigate("/login", { replace: true, state: { from: here } });
      } else if (here !== "/change-password") {
        navigate("/change-password", { replace: true });
      }
    };
    window.addEventListener(AUTH_EVENT, onSignal);
    return () => window.removeEventListener(AUTH_EVENT, onSignal);
  }, [navigate, qc, here]);
  return null;
}

export function FullPage({ children }: { children: React.ReactNode }) {
  return <div className="grid min-h-full place-items-center p-4 text-ink-2">{children}</div>;
}

/** Only for logged-in people; until the first password change, only the password page. */
export function RequireAuth({ passwordPage = false }: { passwordPage?: boolean }) {
  const me = useMe();
  const location = useLocation();
  const token = me.data?.csrf_token;
  useEffect(() => {
    if (token) setCsrfToken(token);
  }, [token]);
  if (me.isPending) return <FullPage>Loading</FullPage>;
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
    }
    return (
      <FullPage>
        <div className="space-y-3 text-center">
          <p>{friendlyError(me.error)}</p>
          <Button onClick={() => void me.refetch()}>Try again</Button>
        </div>
      </FullPage>
    );
  }
  if (me.data.must_change_password && !passwordPage) return <Navigate to="/change-password" replace />;
  return <Outlet />;
}
