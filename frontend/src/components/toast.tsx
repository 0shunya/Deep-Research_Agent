import { CheckCircle2, CircleAlert, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { cx } from "./ui";

type Tone = "success" | "error";
interface Toast { id: number; tone: Tone; message: string }
interface ToastApi { success: (message: string) => void; error: (message: string) => void }

const ToastContext = createContext<ToastApi | null>(null);
let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);
  const push = useCallback((tone: Tone, message: string) => {
    const id = nextId++;
    setToasts((all) => [...all.slice(-3), { id, tone, message }]);
    window.setTimeout(() => dismiss(id), tone === "error" ? 7000 : 3500);
  }, [dismiss]);
  const api = useMemo<ToastApi>(() => ({ success: (m) => push("success", m), error: (m) => push("error", m) }), [push]);
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4 sm:items-end" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} role={t.tone === "error" ? "alert" : "status"}
            className={cx("pointer-events-auto flex w-full max-w-sm items-start gap-2 rounded-md border bg-surface px-3 py-2.5 shadow-lg",
              t.tone === "error" ? "border-bad/30" : "border-line")}>
            {t.tone === "error"
              ? <CircleAlert className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden />
              : <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />}
            <p className="flex-1 text-[13px]">{t.message}</p>
            <button onClick={() => dismiss(t.id)} className="text-ink-3 hover:text-ink" aria-label="Dismiss"><X className="size-4" /></button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}
