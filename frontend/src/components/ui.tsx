// Presentational components only: they never fetch.
import { AlertTriangle, CircleAlert, Info, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

import type { Severity } from "../lib/urgency";

export function SeverityChip({ severity, label }: { severity: Severity; label: string }) {
  if (!label || severity === "none") return null;
  const Icon = severity === "critical" ? CircleAlert : severity === "warning" ? AlertTriangle : Info;
  return (
    <span className={`chip ${severity}`}>
      <Icon size={13} aria-hidden /> {label}
    </span>
  );
}

export function Flag({ children }: { children: ReactNode }) {
  return <span className="chip flag">{children}</span>;
}

export function Badge({ count, critical }: { count: number; critical: boolean }) {
  if (!count) return null;
  return <span className={`badge${critical ? " critical" : ""}`} aria-label={`${count} need attention`}>{count}</span>;
}

export function Dialog({ title, onClose, children, footer, wide }: {
  title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onClick={onClose}>
      <div className={`dialog${wide ? " wide" : ""}`} role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <h2>{title}</h2>
          <button className="btn ghost small close" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

/** Side panel on desktop, full-height bottom sheet on phones. */
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay side" onClick={onClose}>
      <aside className="sheet" role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <h2>{title}</h2>
          <button className="btn ghost small close" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="sheet-body">{children}</div>
      </aside>
    </div>
  );
}

export function Progress({ value, severity }: { value: number; severity: Severity }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return <div className="progress" aria-hidden><span className={severity} style={{ width: `${pct}%` }} /></div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
