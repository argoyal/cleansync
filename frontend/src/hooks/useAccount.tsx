import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";

import { useAccounts } from "../api/queries";
import type { Account } from "../api/types";

const KEY = "drivesync.account";

type AccountCtx = {
  account: Account | null;
  accountId: number | null;
  accounts: Account[];
  setAccountId: (id: number) => void;
};

const AccountContext = createContext<AccountCtx>({
  account: null,
  accountId: null,
  accounts: [],
  setAccountId: () => undefined,
});

function remembered(): number | null {
  try {
    const v = localStorage.getItem(KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

/** The Google account whose files the whole UI shows (switched from the avatar menu). */
export function AccountProvider({ children }: { children: ReactNode }) {
  const { data: accounts = [] } = useAccounts();
  const [chosen, setChosen] = useState<number | null>(remembered);
  const setAccountId = useCallback((id: number) => {
    setChosen(id);
    try {
      localStorage.setItem(KEY, String(id));
    } catch {
      // private window: the choice just isn't remembered
    }
  }, []);
  const account =
    accounts.find((a) => a.id === chosen) ??
    accounts.find((a) => a.status !== "DISCONNECTED") ??
    accounts[0] ??
    null;
  const value = useMemo(
    () => ({ account, accountId: account?.id ?? null, accounts, setAccountId }),
    [account, accounts, setAccountId],
  );
  return <AccountContext.Provider value={value}>{children}</AccountContext.Provider>;
}

export function useCurrentAccount(): AccountCtx {
  return useContext(AccountContext);
}
