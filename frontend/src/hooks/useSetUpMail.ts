import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { OAUTH_RETURN_KEY } from "./useConnect";

/** "Set up mail backup": Google sign-in again with Gmail asked for (spec §3.1, §4.2). */
export function useSetUpMail() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (accountId: number) =>
      api<{ auth_url: string | null }>(`/api/accounts/${accountId}/mail/setup`, { method: "POST" }),
    onSuccess: ({ auth_url }) => {
      if (auth_url === null) {
        // Nothing to sign in to (a pretend account): Mail is ready for the next step.
        void qc.invalidateQueries({ queryKey: ["accounts"] });
        navigate("/mail");
        return;
      }
      try {
        sessionStorage.setItem(OAUTH_RETURN_KEY, "/mail");
      } catch {
        // without storage the person lands on Home afterwards and opens Mail from the rail
      }
      window.location.assign(auth_url);
    },
  });
}
