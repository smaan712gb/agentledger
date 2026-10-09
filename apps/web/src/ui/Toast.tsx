import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

import styles from "./Toast.module.css";

export type ToastTone = "info" | "success" | "error";

export interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastApi {
  toast: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** Transient messages, announced through a live region and removed after a few seconds; errors stay until closed. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);

  const remove = useCallback((id: number) => {
    setItems((all) => all.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, tone: ToastTone = "info") => {
      const id = ++counter.current;
      setItems((all) => [...all.slice(-4), { id, message, tone }]);
      if (tone !== "error") setTimeout(() => remove(id), 6000);
    },
    [remove],
  );

  const api = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className={styles.region} aria-live="polite" aria-atomic="false" aria-label="Notifications" role="region">
        {items.map((t) => (
          <div
            key={t.id}
            className={[styles.toast, styles[t.tone]].join(" ")}
            role={t.tone === "error" ? "alert" : "status"}
          >
            <span>{t.message}</span>
            <button
              type="button"
              className={styles.close}
              onClick={() => remove(t.id)}
              aria-label="Dismiss notification"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast needs a ToastProvider");
  return ctx;
}
