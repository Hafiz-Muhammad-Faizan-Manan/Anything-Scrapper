"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

import { AlertIcon, CheckIcon, CloseIcon, InfoIcon } from "./Icons";

export type ToastKind = "success" | "error" | "info";

interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  description?: string;
}

interface ToastApi {
  notify: (kind: ToastKind, title: string, description?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const STYLES: Record<ToastKind, { ring: string; icon: ReactNode }> = {
  success: { ring: "border-emerald-500/40", icon: <CheckIcon className="text-emerald-400" /> },
  error: { ring: "border-rose-500/40", icon: <AlertIcon className="text-rose-400" /> },
  info: { ring: "border-sky-500/40", icon: <InfoIcon className="text-sky-400" /> },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const notify = useCallback(
    (kind: ToastKind, title: string, description?: string) => {
      const id = nextId.current++;
      setToasts((t) => [...t.slice(-3), { id, kind, title, description }]);
      setTimeout(() => dismiss(id), kind === "error" ? 8000 : 4500);
    },
    [dismiss],
  );

  const api = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex flex-col items-center gap-2 p-4 sm:items-end"
        aria-live="polite"
        role="status"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`toast-in pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border ${STYLES[t.kind].ring} bg-slate-900/95 p-4 shadow-2xl shadow-black/40 backdrop-blur`}
          >
            <span className="mt-0.5 shrink-0">{STYLES[t.kind].icon}</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-slate-100">{t.title}</p>
              {t.description && <p className="mt-0.5 break-words text-sm text-slate-400">{t.description}</p>}
            </div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="shrink-0 rounded-md p-1 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200"
              aria-label="Dismiss notification"
            >
              <CloseIcon width={16} height={16} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}
