import { useMutation } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { api } from "../api/client";

export const OAUTH_RETURN_KEY = "drivesync.oauthReturn";

/** Start Google sign-in (new account, or reconnect one); Google sends the browser back to "/". */
export function useConnectGoogle() {
  const location = useLocation();
  return useMutation({
    mutationFn: (accountId?: number) =>
      api<{ auth_url: string }>(accountId ? `/api/accounts/${accountId}/reconnect` : "/api/accounts/connect", {
        method: "POST",
      }),
    onSuccess: ({ auth_url }) => {
      try {
        sessionStorage.setItem(OAUTH_RETURN_KEY, location.pathname + location.search);
      } catch {
        // without storage the person lands on Home afterwards, which is fine
      }
      window.location.assign(auth_url);
    },
  });
}
