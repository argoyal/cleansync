import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, friendlyError, setCsrfToken } from "../api/client";
import { useMe } from "../api/queries";
import type { Me } from "../api/types";
import { Button, Field, inputClass, Notice } from "../components/ui";
import { AuthCard } from "./LoginPage";

export const MIN_PASSWORD = 12;

/** The form on its own: used on the forced first-login page and in Settings, Security. */
export function PasswordForm({ onDone, submitLabel = "Change password" }: { onDone: () => void; submitLabel?: string }) {
  const qc = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const problem =
    next && next.length < MIN_PASSWORD
      ? `Use at least ${MIN_PASSWORD} characters.`
      : again && next !== again
        ? "The two new passwords are different."
        : null;
  const change = useMutation({
    mutationFn: () =>
      api<Me>("/api/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      }),
    onSuccess: (me) => {
      setCsrfToken(me.csrf_token);
      qc.setQueryData(["me"], me);
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!problem) change.mutate();
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Current password">
        <input className={inputClass} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      </Field>
      <Field label="New password" hint={`At least ${MIN_PASSWORD} characters. A short sentence works well.`}>
        <input className={inputClass} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
      </Field>
      <Field label="New password again">
        <input className={inputClass} type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
      </Field>
      {problem && <p className="text-table text-err">{problem}</p>}
      {change.isError && <Notice tone="err">{friendlyError(change.error)}</Notice>}
      <Button tone="primary" type="submit" disabled={change.isPending || !current || !next || next !== again || !!problem}>
        {submitLabel}
      </Button>
    </form>
  );
}

export function ChangePasswordPage() {
  const navigate = useNavigate();
  const me = useMe();
  const forced = me.data?.must_change_password ?? true;
  return (
    <AuthCard title={forced ? "Choose your own password" : "Change password"}>
      {forced && (
        <p className="mb-6 text-ink-2">
          You logged in with the starting password. Pick a new one before going on; it signs out every other session.
        </p>
      )}
      <PasswordForm onDone={() => navigate("/", { replace: true })} submitLabel="Save password" />
    </AuthCard>
  );
}
