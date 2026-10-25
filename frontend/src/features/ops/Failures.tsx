// Failures and Degradations (PDS page 7; Q3, Q7): stage → code → fingerprint with units, recovered vs final and
// a trend; a second tab for the silent fallbacks.
import { Link, useSearchParams } from "react-router-dom";

import { RANGES, useOps, useOpsWindow } from "../../lib/ops";
import { COLOR, fmtN, StackedBars } from "./charts";
import { Panel, RunLink, State, useKeep } from "./common";

interface Failure {
  stage: string; code: string; fingerprint: string; error_type: string | null; name: string; sample: string | null; runs: number; units: number;
  recovered: number; final: number; first_seen: string; last_seen: string; example_run_ids: string[]; trend: { t: string; n: number }[];
}
interface Degradation { reason: string; count: number; runs: number; cost_only: boolean | null; description: string | null; first_seen: string; last_seen: string }

export function OpsFailures() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") === "degradations" ? "degradations" : "failures";
  const setTab = (t: string) => { const n = new URLSearchParams(sp); if (t === "failures") n.delete("tab"); else n.set("tab", t); setSp(n, { replace: true }); };
  return (
    <>
      <div className="segmented" role="tablist" aria-label="Failures or fallbacks">
        <button role="tab" aria-selected={tab === "failures"} className={tab === "failures" ? "on" : ""} onClick={() => setTab("failures")}>Failures</button>
        <button role="tab" aria-selected={tab === "degradations"} className={tab === "degradations" ? "on" : ""} onClick={() => setTab("degradations")}>Silent fallbacks</button>
      </div>
      {tab === "failures" ? <FailureTree /> : <Degradations />}
    </>
  );
}

function FailureTree() {
  const { range, params } = useOpsWindow();
  const keep = useKeep();
  const q = useOps<{ failures: Failure[] }>("/failures", params);
  const rows = q.data?.failures ?? [];
  const stages = [...new Set(rows.map((r) => r.stage))];
  const buckets = [...new Set(rows.flatMap((r) => r.trend.map((t) => t.t)))].sort();
  return (
    <State q={q} empty={!rows.length}>
      <Panel title="Failed attempts over time" wide>
        <StackedBars spanS={RANGES[range]} keys={[{ key: "n", label: "Failed attempts", color: COLOR.failed }]}
          rows={buckets.map((t) => ({ t: Date.parse(t), n: rows.reduce((s, r) => s + (r.trend.find((x) => x.t === t)?.n ?? 0), 0) }))} />
      </Panel>
      {stages.map((stage) => (
        <Panel key={stage} title={`Stage: ${stage}`} wide>
          <table className="table">
            <thead><tr><th>Code · where</th><th>Reason (sample)</th><th className="num">Runs</th><th className="num">Units</th><th className="num">Recovered</th><th className="num">Failed for good</th><th>First / last seen</th><th>Examples</th></tr></thead>
            <tbody>{rows.filter((r) => r.stage === stage).map((r) => (
              <tr key={r.fingerprint + r.name}>
                <td><strong>{r.code}</strong><div className="muted small">{r.name} · {r.error_type}</div></td>
                <td className="small">{r.sample}</td>
                <td className="num"><Link to={keep("runs", { fingerprint: r.fingerprint })}>{fmtN(r.runs)}</Link></td>
                <td className="num">{fmtN(r.units)}</td><td className="num">{fmtN(r.recovered)}</td>
                <td className="num">{r.final ? <span className="chip critical">{fmtN(r.final)}</span> : "0"}</td>
                <td className="small">{new Date(r.first_seen).toLocaleString("en-IN")}<br />{new Date(r.last_seen).toLocaleString("en-IN")}</td>
                <td>{r.example_run_ids.slice(0, 3).map((id) => <div key={id}><RunLink id={id} /></div>)}</td>
              </tr>))}</tbody>
          </table>
        </Panel>
      ))}
    </State>
  );
}

function Degradations() {
  const { params } = useOpsWindow();
  const keep = useKeep();
  const q = useOps<{ degradations: Degradation[] }>("/degradations", params);
  const rows = q.data?.degradations ?? [];
  return (
    <State q={q} empty={!rows.length}>
      <Panel title="Silent fallbacks: the run succeeded, but something gave way" wide>
        <table className="table">
          <thead><tr><th>Reason</th><th>What happened</th><th className="num">Count</th><th className="num">Runs</th><th>First / last seen</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.reason}>
              <td><Link to={keep("runs", { reason: r.reason })}>{r.reason}</Link> {r.cost_only ? <span className="chip calm">cost only</span> : <span className="chip warning">turns the run amber</span>}</td>
              <td className="small">{r.description}</td><td className="num">{fmtN(r.count)}</td><td className="num">{fmtN(r.runs)}</td>
              <td className="small">{new Date(r.first_seen).toLocaleString("en-IN")}<br />{new Date(r.last_seen).toLocaleString("en-IN")}</td>
            </tr>))}</tbody>
        </table>
      </Panel>
    </State>
  );
}
