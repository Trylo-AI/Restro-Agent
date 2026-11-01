// Toasts and the 5-second Undo toast (docs/console.md#undo-toast). One undo at a time; a new one replaces it.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

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

/** Seconds left on an Undo toast; ticks on its own so the provider (and every screen) doesn't re-render. */
function Countdown({ until }: { until: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);
  return <span className="count">{Math.max(0, Math.ceil((until - now) / 1000))} s</span>;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef(new Map<number, number>());

  const remove = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
  }, []);

  // Stable: every screen uses toasts, so a new object per render would re-render them all with each toast.
  const api = useMemo<ToastApi>(() => ({
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
  }), [remove]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.error ? " error" : ""}`}>
            <span>{t.text}</span>
            {t.undo && <Countdown until={t.undo.until} />}
            {t.undo && (
              <button onClick={async () => { remove(t.id); await t.undo!.run(); }}>Undo</button>
            )}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
