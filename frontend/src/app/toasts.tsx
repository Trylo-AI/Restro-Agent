// Toasts and the 5-second Undo toast (docs/console.md#undo-toast). One undo at a time; a new one replaces it.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

interface Toast {
  id: number;
  text: string;
  error?: boolean;
  undo?: { until: number; run: () => Promise<void> };
}

interface ToastApi {
  show(text: string, error?: boolean): void;
  undo(text: string, run: () => Promise<void>, seconds?: number): void;
  clearUndo(): void;
}

const Ctx = createContext<ToastApi | null>(null);
export const useToasts = (): ToastApi => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToasts outside ToastProvider");
  return v;
};

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [now, setNow] = useState(Date.now());
  const timers = useRef(new Map<number, number>());

  const remove = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
  }, []);

  const api: ToastApi = {
    show(text, error) {
      const id = nextId++;
      setToasts((t) => [...t.filter((x) => !x.error || error), { id, text, error }].slice(-3));
      timers.current.set(id, window.setTimeout(() => remove(id), error ? 6000 : 3500));
    },
    undo(text, run, seconds = 5) {
      const id = nextId++;
      setToasts((t) => [...t.filter((x) => !x.undo), { id, text, undo: { until: Date.now() + seconds * 1000, run } }]);
      timers.current.set(id, window.setTimeout(() => remove(id), seconds * 1000));
    },
    clearUndo() {
      setToasts((t) => t.filter((x) => !x.undo));
    },
  };

  useEffect(() => {
    if (!toasts.some((t) => t.undo)) return;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [toasts]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.error ? " error" : ""}`}>
            <span>{t.text}</span>
            {t.undo && <span className="count">{Math.max(0, Math.ceil((t.undo.until - now) / 1000))} s</span>}
            {t.undo && (
              <button onClick={async () => { remove(t.id); await t.undo!.run(); }}>Undo</button>
            )}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
