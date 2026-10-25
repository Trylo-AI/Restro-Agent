// /platform/ops: the performance dashboard (docs/OPS_PLAN.md). Token gate, page tabs, time range and service in
// the URL. Every page reads /api/ops and every number links one level down, ending at a run trace.
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useSearchParams } from "react-router-dom";

import { opsToken, RANGES, setOpsToken, useOps, type Range } from "../../lib/ops";
import { useKeep } from "./common";

const PAGES = [
  ["overview", "Overview"], ["live", "Live"], ["runs", "Runs"], ["failures", "Failures"], ["latency", "Latency"],
  ["endpoints", "Endpoints"], ["dependencies", "Dependencies"], ["efficiency", "Efficiency"], ["insights", "Insights"],
  ["logs", "Logs"],
] as const;

interface Meta { tables_ok: boolean; tracing_enabled: boolean; git_sha: string | null; env: string; features: Record<string, boolean> }

function TokenForm({ onSet, failed }: { onSet: () => void; failed: string | null }) {
  const [value, setValue] = useState("");
  return (
    <form className="ops-token" onSubmit={(e) => { e.preventDefault(); setOpsToken(value.trim()); onSet(); }}>
      <h2><KeyRound size={18} /> Performance dashboard</h2>
      <p className="muted">Paste the ops token: <code>OPS_DASHBOARD_TOKEN</code> in <code>backend/.env</code>. It stays in this tab only.</p>
      {failed && <p className="error-text">{failed}</p>}
      <label className="field">Ops token
        <input type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
      </label>
      <button className="btn primary" disabled={!value.trim()}>Open dashboard</button>
    </form>
  );
}

export function OpsLayout() {
  const [, rerender] = useState(0);
  const [sp, setSp] = useSearchParams();
  const keep = useKeep();
  const hasToken = !!opsToken();
  const meta = useOps<Meta>("/meta", {}, { enabled: hasToken });

  if (!hasToken || meta.error?.status === 401) {
    return <TokenForm failed={meta.error?.status === 401 ? meta.error.message : null} onSet={() => { void meta.refetch(); rerender((n) => n + 1); }} />;
  }
  if (meta.error) return <div className="viz-empty error-text">{meta.error.message}</div>;

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(sp);
    if (v) next.set(k, v);
    else next.delete(k);
    setSp(next, { replace: true });
  };

  return (
    <div className="ops">
      <div className="ops-bar">
        <nav className="ops-tabs" aria-label="Performance pages">
          {PAGES.map(([path, label]) => <NavLink key={path} to={keep(path)} className={({ isActive }) => (isActive ? "on" : "")}>{label}</NavLink>)}
        </nav>
        <div className="ops-filters">
          <div className="segmented" role="radiogroup" aria-label="Time range">
            {(Object.keys(RANGES) as Range[]).map((r) => (
              <button key={r} role="radio" aria-checked={(sp.get("range") ?? "24h") === r} className={(sp.get("range") ?? "24h") === r ? "on" : ""}
                onClick={() => set("range", r === "24h" ? "" : r)}>{r}</button>
            ))}
          </div>
          <select aria-label="Service" value={sp.get("service") ?? ""} onChange={(e) => set("service", e.target.value)}>
            <option value="">api + worker</option><option value="api">api</option><option value="worker">worker</option>
          </select>
          <span className="muted small">
            {meta.data ? <>git {meta.data.git_sha?.slice(0, 7) ?? "?"} · {meta.data.env}{meta.data.tracing_enabled ? "" : " · tracing off"}</> : "…"}
          </span>
          <button className="btn small ghost" onClick={() => { setOpsToken(null); rerender((n) => n + 1); }}>Forget token</button>
        </div>
      </div>
      {meta.data && !meta.data.tables_ok
        ? <div className="banner warning">Tracing tables missing: apply supabase/migrations/20261024000006_ops_tracing.sql.</div>
        : <Outlet />}
    </div>
  );
}
