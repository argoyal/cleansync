import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";

import { AUTH_EVENT, type AuthSignal, setCsrfToken } from "../api/client";
import { useMe } from "../api/queries";

/** Sends the person to the login or password page whenever the API says so. */
export function AuthEvents() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  useEffect(() => {
    const onSignal = (e: Event) => {
      const kind = (e as CustomEvent<AuthSignal>).detail;
      if (kind === "login") {
        qc.clear();
        navigate("/login", { replace: true });
      } else {
        navigate("/change-password", { replace: true });
      }
    };
    window.addEventListener(AUTH_EVENT, onSignal);
    return () => window.removeEventListener(AUTH_EVENT, onSignal);
  }, [navigate, qc]);
  return null;
}

export function FullPage({ children }: { children: React.ReactNode }) {
  return <div className="grid min-h-full place-items-center p-4 text-ink-2">{children}</div>;
}

/** Only for logged-in people; until the first password change, only the password page. */
export function RequireAuth({ passwordPage = false }: { passwordPage?: boolean }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <FullPage>Loading</FullPage>;
  if (me.isError) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  setCsrfToken(me.data.csrf_token); // same value every render; mutations need it at once
  if (me.data.must_change_password && !passwordPage) return <Navigate to="/change-password" replace />;
  return <Outlet />;
}
