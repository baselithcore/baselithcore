import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Icon, paths } from "../lib/icons";

type ToastTone = "info" | "success" | "error";

interface ToastItem {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
}

interface ToastInput {
  title: string;
  description?: string;
  tone?: ToastTone;
}

interface ToastContextValue {
  push: (toast: ToastInput) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((toast: ToastInput) => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    const next: ToastItem = {
      id,
      title: toast.title,
      description: toast.description,
      tone: toast.tone ?? "info",
    };
    setItems((prev) => [...prev, next]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((item) => item.id !== id));
    }, 3200);
  }, []);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="true">
        {items.map((item) => (
          <div key={item.id} className={`toast ${item.tone}`}>
            <div className="toast-head">
              <span className="toast-title">
                <span className="toast-icon" aria-hidden="true">
                  <Icon
                    path={
                      item.tone === "success"
                        ? paths.check
                        : item.tone === "error"
                          ? paths.x
                          : paths.sparkles
                    }
                    size={12}
                  />
                </span>
                {item.title}
              </span>
              <button
                type="button"
                className="toast-close"
                aria-label="Dismiss notification"
                onClick={() => dismiss(item.id)}
              >
                <Icon path={paths.x} size={12} />
              </button>
            </div>
            {item.description && (
              <div className="toast-description">{item.description}</div>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToasts() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToasts must be used inside ToastProvider");
  return ctx;
}
