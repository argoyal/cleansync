import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";

type Toast = { id: number; text: string; tone: "info" | "error"; action?: { label: string; run: () => void } };
type Push = (text: string, opts?: { tone?: "info" | "error"; action?: Toast["action"] }) => void;

const ToastContext = createContext<Push>(() => undefined);

export function useToast(): Push {
  return useContext(ToastContext);
}

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback<Push>(
    (text, opts = {}) => {
      const id = nextId++;
      setToasts((t) => [...t.slice(-3), { id, text, tone: opts.tone ?? "info", action: opts.action }]);
      window.setTimeout(() => dismiss(id), opts.tone === "error" ? 9000 : 5000);
    },
    [dismiss],
  );
  const value = useMemo(() => push, [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-20 md:bottom-4 left-4 z-[60] flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.tone === "error" ? "alert" : "status"}
            className={`pointer-events-auto flex max-w-sm items-center gap-4 rounded-ctl px-4 py-3 text-body shadow-float ${
              t.tone === "error" ? "bg-err text-white" : "bg-ink text-bg"
            }`}
          >
            <span className="flex-1">{t.text}</span>
            {t.action && (
              <button
                type="button"
                className="font-semibold underline-offset-2 hover:underline"
                onClick={() => {
                  dismiss(t.id);
                  t.action?.run();
                }}
              >
                {t.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
