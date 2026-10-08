import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { api, friendlyError, setCsrfToken } from "../api/client";
import { useHealth } from "../api/queries";
import type { Me } from "../api/types";
import { Icon } from "../components/Icon";
import { Button, Field, inputClass, Notice } from "../components/ui";
import { DEFAULT_APP_NAME } from "../config";

export function Brand({ big = false }: { big?: boolean }) {
  const name = useHealth().data?.app_name ?? DEFAULT_APP_NAME;
  useEffect(() => {
    document.title = name;
  }, [name]);
  return (
    <span className={`flex items-center gap-2.5 font-semibold ${big ? "text-title" : "text-section"}`}>
      <span className="grid h-8 w-8 place-items-center rounded-ctl bg-accent text-accent-ink">
        <Icon name="cloud_done" className="text-[20px]" />
      </span>
      {name}
    </span>
  );
}

export function AuthCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="grid min-h-full place-items-center bg-bg px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8">
          <Brand big />
        </div>
        <h1 className="mb-6 text-hero font-semibold tracking-tight">{title}</h1>
        {children}
      </div>
    </main>
  );
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => api<Me>("/api/auth/login", { method: "POST", body: { username, password } }),
    onSuccess: (me) => {
      setCsrfToken(me.csrf_token);
      qc.setQueryData(["me"], me);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(me.must_change_password ? "/change-password" : from || "/", { replace: true });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };
  return (
    <AuthCard title="Log in">
      <form onSubmit={submit} className="space-y-4">
        <Field label="Username">
          <input className={inputClass} value={username} autoComplete="username" onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="Password">
          <input
            className={inputClass}
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {login.isError && <Notice tone="err">{friendlyError(login.error)}</Notice>}
        <Button tone="primary" type="submit" disabled={login.isPending || !password} className="w-full justify-center">
          Log in
        </Button>
      </form>
    </AuthCard>
  );
}
