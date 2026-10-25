// Shared pieces for the performance pages: outcome chips, KPI tiles, run links, empty / error states.
import { AlertTriangle, CheckCircle2, CircleSlash, Loader2, RotateCcw, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { OpsError } from "../../lib/ops";

const OUTCOME: Record<string, { label: string; cls: string; icon: ReactNode }> = {
  SUCCEEDED: { label: "Succeeded", cls: "ok", icon: <CheckCircle2 size={13} /> },
  SUCCEEDED_DEGRADED: { label: "Degraded", cls: "warning", icon: <AlertTriangle size={13} /> },
  FAILED: { label: "Failed", cls: "critical", icon: <XCircle size={13} /> },
  ABANDONED: { label: "Abandoned", cls: "critical", icon: <XCircle size={13} /> },
  RETRYING: { label: "Retrying", cls: "warning", icon: <RotateCcw size={13} /> },
  REJECTED: { label: "Rejected", cls: "calm", icon: <CircleSlash size={13} /> },
  CANCELLED: { label: "Cancelled", cls: "calm", icon: <CircleSlash size={13} /> },
  SUPERSEDED: { label: "Superseded", cls: "calm", icon: <CircleSlash size={13} /> },
  RUNNING: { label: "Running", cls: "info", icon: <Loader2 size={13} /> },
};

export function OutcomeChip({ outcome }: { outcome: string }) {
  const o = OUTCOME[outcome] ?? { label: outcome, cls: "calm", icon: null };
  return <span className={`chip ${o.cls}`}>{o.icon}{o.label}</span>;
}

/** Keeps range and service when moving between pages. */
export function useKeep(): (path: string, extra?: Record<string, string | undefined>) => string {
  const [sp] = useSearchParams();
  return (path, extra = {}) => {
    const q = new URLSearchParams();
    for (const k of ["range", "service"]) {
      const v = sp.get(k);
      if (v) q.set(k, v);
    }
    for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== "") q.set(k, v);
    const s = q.toString();
    return `/platform/ops/${path}${s ? `?${s}` : ""}`;
  };
}

export function RunLink({ id, children }: { id: string; children?: ReactNode }) {
  const keep = useKeep();
  return <Link to={keep(`runs/${id}`)} className="mono">{children ?? id.slice(0, 8)}</Link>;
}

export function Tile({ label, value, sub, to, tone }: { label: string; value: ReactNode; sub?: ReactNode; to?: string; tone?: "ok" | "warning" | "critical" }) {
  const body = (
    <>
      <div className="tile-label">{label}</div>
      <div className={`tile-value${tone ? ` tone-${tone}` : ""}`}>{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </>
  );
  return to ? <Link className="tile" to={to}>{body}</Link> : <div className="tile">{body}</div>;
}

/** Trend arrow vs the previous window. `worseWhenUp`: a rise is bad (latency, failures). */
export function Delta({ now, before, worseWhenUp = true }: { now: number | null | undefined; before: number | null | undefined; worseWhenUp?: boolean }) {
  if (now === null || now === undefined || !before) return <span className="muted">no earlier data</span>;
  const pct = ((now - before) / before) * 100;
  if (Math.abs(pct) < 1) return <span className="muted">≈ same as before</span>;
  const bad = (pct > 0) === worseWhenUp;
  return <span className={bad ? "delta-bad" : "delta-good"}>{pct > 0 ? "▲" : "▼"} {Math.abs(pct).toFixed(0)} % vs previous</span>;
}

export function Panel({ title, children, right, wide }: { title: string; children: ReactNode; right?: ReactNode; wide?: boolean }) {
  return (
    <section className={`panel${wide ? " wide" : ""}`}>
      <div className="panel-head"><h2>{title}</h2>{right}</div>
      {children}
    </section>
  );
}

export function State({ q, empty, children }: { q: { isPending: boolean; error: OpsError | null; data?: unknown }; empty?: boolean; children: ReactNode }) {
  if (q.isPending) return <div className="viz-empty">Loading…</div>;
  if (q.error) return <div className="viz-empty error-text">{q.error.message}</div>;
  if (empty) return <div className="viz-empty">No data in this range.</div>;
  return <>{children}</>;
}

export function Attrs({ attrs }: { attrs: Record<string, unknown> | null | undefined }) {
  const entries = Object.entries(attrs ?? {});
  if (!entries.length) return <span className="muted">—</span>;
  return (
    <dl className="attrs">
      {entries.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>)}
    </dl>
  );
}
