// Insights (PDS page 11; Q9): the improvement backlog, ranked by estimated saving, each with evidence, a fix hint
// and example traces; dismiss with a note. Logs (page 13; Q13): the local JSON-lines logs, filterable, follow mode.
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { opsPost, useOps, type Finding } from "../../lib/ops";
import { fmtMs } from "./charts";
import { Attrs, Panel, RunLink, State } from "./common";

const DETECTOR: Record<string, string> = {
  n_plus_one: "N+1 queries", chatty_dependency: "Chatty dependency", latency_regression: "Regression after deploy",
  new_failure: "New failure", error_spike: "Error spike", retry_waste: "Retry waste", slow_tail: "Slow tail",
  timeout_near_limit: "Near a timeout", degradation_creep: "Fallbacks rising", payload_bloat: "Prompt bloat",
  slowest_statement: "Slowest statements",
};

export function OpsInsights() {
  const [showDismissed, setShowDismissed] = useState(false);
  const q = useOps<{ window: { from: string; to: string }; findings: Finding[] }>("/insights", { dismissed: showDismissed ? "true" : undefined });
  const qc = useQueryClient();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const act = async (f: Finding, path: "dismiss" | "restore") => {
    await opsPost(`/insights/${encodeURIComponent(f.key)}/${path}`, path === "dismiss" ? { note: notes[f.key] || null } : {});
    void qc.invalidateQueries({ queryKey: ["ops"] });
  };
  const rows = q.data?.findings ?? [];
  return (
    <>
      <div className="ops-filter-row">
        <span className="muted small">Last 7 days · ranked by estimated saving. Candidates need a person to confirm.</span>
        <label className="row small"><input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} /> show dismissed</label>
      </div>
      <State q={q} empty={!rows.length}>
        {rows.map((f) => (
          <Panel key={f.key} wide title={`${DETECTOR[f.detector] ?? f.detector}: ${f.title}`} right={
            <span className="row">
              <span className={`chip ${f.severity === "high" ? "critical" : f.severity === "medium" ? "warning" : "calm"}`}>{f.severity}</span>
              {f.confidence === "candidate" && <span className="chip calm">candidate</span>}
              {f.estimated_saving.ms_per_day ? <span className="chip info">saves ≈ {fmtMs(f.estimated_saving.ms_per_day)} / day</span> : null}
            </span>}>
            <div className="finding">
              <div><div className="muted small">Where</div><Attrs attrs={f.where} /></div>
              <div><div className="muted small">Evidence</div><Attrs attrs={f.evidence} /></div>
              <div>
                <div className="muted small">How to fix</div><p>{f.fix}</p>
                {f.example_run_ids.length > 0 && <p className="small">Examples: {f.example_run_ids.map((id) => <span key={id}><RunLink id={id} /> </span>)}</p>}
                {f.dismissed ? (
                  <p className="small">Dismissed{f.dismissed.note ? `: ${f.dismissed.note}` : ""} <button className="btn small" onClick={() => void act(f, "restore")}>Restore</button></p>
                ) : (
                  <div className="row">
                    <input aria-label="Why dismiss" placeholder="why (optional)" maxLength={500} value={notes[f.key] ?? ""} onChange={(e) => setNotes({ ...notes, [f.key]: e.target.value })} />
                    <button className="btn small" onClick={() => void act(f, "dismiss")}>Dismiss</button>
                  </div>
                )}
              </div>
            </div>
          </Panel>
        ))}
      </State>
    </>
  );
}

interface LogLine { timestamp: string; level: string; event: string; service?: string; run_id?: string; span?: string; logger?: string; [k: string]: unknown }

export function OpsLogs() {
  const [sp, setSp] = useSearchParams();
  const [follow, setFollow] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const f = { level: sp.get("level") ?? undefined, run_id: sp.get("run_id") ?? undefined, service: sp.get("service") ?? undefined, q: sp.get("q") ?? undefined };
  const q = useOps<{ available: boolean; lines: LogLine[] }>("/logs", { ...f, limit: 300 }, { refetchMs: follow ? 3000 : undefined });
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n, { replace: true }); };
  return (
    <>
      <div className="ops-filter-row">
        <select aria-label="Level" value={f.level ?? ""} onChange={(e) => set("level", e.target.value)}>
          <option value="">every level</option><option value="error">error</option><option value="warning,error">warning + error</option><option value="info">info</option>
        </select>
        <input aria-label="Run id" placeholder="run id" value={f.run_id ?? ""} onChange={(e) => set("run_id", e.target.value.trim())} />
        <input aria-label="Text" placeholder="event or field text" value={f.q ?? ""} onChange={(e) => set("q", e.target.value)} />
        <label className="row small"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
      </div>
      <State q={q} empty={q.data?.available === true && !q.data.lines.length}>
        {q.data && !q.data.available ? <div className="viz-empty">No local log files (backend/logs). In the cloud, search Cloud Logging by run_id.</div> : (
          <Panel title="Newest first (ids only: logs never hold message text, phones or tokens)" wide>
            <table className="table logs">
              <tbody>{q.data?.lines.map((l, i) => {
                const id = `${l.timestamp}-${i}`;
                const extras = Object.fromEntries(Object.entries(l).filter(([k]) => !["timestamp", "level", "event", "service", "run_id", "span", "_file"].includes(k)));
                return (
                  <tr key={id} onClick={() => setOpen(open === id ? null : id)} className={`lvl-${l.level}`}>
                    <td className="small mono">{l.timestamp.slice(11, 23)}</td>
                    <td><span className={`chip ${l.level === "error" ? "critical" : l.level === "warning" ? "warning" : "calm"}`}>{l.level}</span></td>
                    <td className="small">{l.service}</td>
                    <td>{l.event}{l.span ? <span className="muted small"> · {l.span}</span> : null}
                      {open === id && <Attrs attrs={extras} />}</td>
                    <td>{l.run_id ? <RunLink id={l.run_id} /> : null}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </Panel>
        )}
      </State>
    </>
  );
}
