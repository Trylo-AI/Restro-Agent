// Endpoints (PDS page 3; Q10), Dependencies (page 9; Q5, Q9) and Efficiency (page 10; Q8, Q9; tokens, not money,
// until prices are tracked).
import { useState } from "react";
import { Link } from "react-router-dom";

import { RANGES, useOps, useOpsWindow } from "../../lib/ops";
import { COLOR, fmtMs, fmtN, fmtPct, Spark, StackedBars, TimeChart } from "./charts";
import { Panel, RunLink, State, Tile, useKeep } from "./common";

interface Endpoint {
  kind: string; name: string; units: number; runs: number; succeeded: number; failed: number; rejected: number; degraded: number;
  p50: number | null; p95: number | null; p99: number | null; per_min: number; success_rate: number | null; reject_rate: number | null;
  total_ms: number; trend: { t: string; n: number; p95: number | null }[];
}

type SortKey = "units" | "p95" | "failed" | "total_ms";

export function OpsEndpoints() {
  const { params } = useOpsWindow();
  const keep = useKeep();
  const q = useOps<{ endpoints: Endpoint[] }>("/endpoints", params);
  const [sort, setSort] = useState<SortKey>("units");
  const rows = [...(q.data?.endpoints ?? [])].sort((a, b) => Number(b[sort] ?? 0) - Number(a[sort] ?? 0));
  const th = (k: SortKey, label: string) => <th className="num"><button className={`link${sort === k ? " on" : ""}`} onClick={() => setSort(k)}>{label}{sort === k ? " ▼" : ""}</button></th>;
  return (
    <State q={q} empty={!rows.length}>
      <Panel title="Every entry point: webhooks, staff API, turns, tasks, timers, jobs" wide>
        <table className="table">
          <thead><tr><th>Name</th>{th("units", "Units")}<th className="num">/ min</th><th className="num">Success</th><th className="num">Rejected</th>{th("failed", "Failed")}<th className="num">p50</th>{th("p95", "p95")}<th className="num">p99</th>{th("total_ms", "Total time")}<th>p95 trend</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.name}>
              <td><Link to={keep("latency", { name: r.name })}>{r.name}</Link><div className="muted small">{r.kind}</div></td>
              <td className="num"><Link to={keep("runs", { name: r.name })}>{fmtN(r.units)}</Link></td><td className="num">{r.per_min.toFixed(2)}</td>
              <td className="num">{fmtPct(r.success_rate)}</td><td className="num">{fmtPct(r.reject_rate)}</td>
              <td className="num">{r.failed ? <Link to={keep("runs", { name: r.name, outcome: "FAILED,ABANDONED" })} className="delta-bad">{fmtN(r.failed)}</Link> : "0"}</td>
              <td className="num">{fmtMs(r.p50)}</td><td className="num">{fmtMs(r.p95)}</td><td className="num">{fmtMs(r.p99)}</td><td className="num">{fmtMs(r.total_ms)}</td>
              <td><Spark values={r.trend.map((t) => t.p95)} label={`${r.name} p95`} /></td>
            </tr>))}</tbody>
        </table>
      </Panel>
    </State>
  );
}

interface Target { kind: string; target: string; calls: number; runs: number; errors: number; p50: number | null; p95: number | null; calls_per_run: number | null; error_rate: number | null; per_day: number; ms_per_day: number; total_ms: number }
interface Statement { statement: string; operation: string; tbl: string | null; calls: number; runs: number; mean_ms: number; total_ms: number; p95: number }
interface TargetDetail { trend: { t: string; calls: number; p50: number | null; p95: number | null; errors: number }[]; slowest: { run_id: string; run_name: string; name: string; duration_ms: number; outcome: string; error: string | null }[]; by_run: { name: string; calls: number; runs: number; total_ms: number }[] }

const KIND: Record<string, string> = { db: "Postgres", llm: "Gemini", http_out: "WhatsApp / HTTP", queue: "Queue" };

export function OpsDependencies() {
  const { range, params } = useOpsWindow();
  const q = useOps<{ targets: Target[]; statements: Statement[] }>("/dependencies", params);
  const [pick, setPick] = useState<Target | null>(null);
  const detail = useOps<TargetDetail>(`/dependencies/${encodeURIComponent(pick?.kind ?? "")}/${encodeURIComponent(pick?.target ?? "")}`, params, { enabled: !!pick });
  return (
    <State q={q} empty={!q.data?.targets.length}>
      <Panel title="Calls per target" wide>
        <table className="table">
          <thead><tr><th>Dependency</th><th>Target</th><th className="num">Calls</th><th className="num">Calls / run</th><th className="num">p50</th><th className="num">p95</th><th className="num">Errors</th><th className="num">Time / day</th></tr></thead>
          <tbody>{q.data?.targets.map((t) => (
            <tr key={t.kind + t.target} className={pick?.target === t.target && pick.kind === t.kind ? "current" : ""}>
              <td>{KIND[t.kind] ?? t.kind}</td><td><button className="link" onClick={() => setPick(t)}>{t.target}</button></td>
              <td className="num">{fmtN(t.calls)}</td><td className="num">{t.calls_per_run}</td><td className="num">{fmtMs(t.p50)}</td><td className="num">{fmtMs(t.p95)}</td>
              <td className="num">{t.errors ? <span className="delta-bad">{fmtN(t.errors)} ({fmtPct(t.error_rate)})</span> : "0"}</td><td className="num">{fmtMs(t.ms_per_day)}</td>
            </tr>))}</tbody>
        </table>
        <p className="muted small">For Postgres the target is the table (or the statement type when none is named). A median near the network round trip means the time is the trip, not the SQL.</p>
      </Panel>
      {pick && (
        <State q={detail}>
          {detail.data && (
            <div className="panels">
              <Panel title={`${KIND[pick.kind] ?? pick.kind} · ${pick.target}: p50 / p95`}>
                <TimeChart spanS={RANGES[range]} lines={[
                  { key: "p50", label: "p50", color: COLOR.p50, points: detail.data.trend.map((t) => ({ t: Date.parse(t.t), v: t.p50 })) },
                  { key: "p95", label: "p95", color: COLOR.p95, points: detail.data.trend.map((t) => ({ t: Date.parse(t.t), v: t.p95 })) },
                ]} />
              </Panel>
              <Panel title="Calls per interval">
                <StackedBars spanS={RANGES[range]} keys={[{ key: "ok", label: "Calls", color: COLOR.bar }, { key: "errors", label: "Errors", color: COLOR.failed }]}
                  rows={detail.data.trend.map((t) => ({ t: Date.parse(t.t), ok: t.calls - t.errors, errors: t.errors }))} />
              </Panel>
              <Panel title="Who calls it">
                <table className="table"><tbody>{detail.data.by_run.map((r) => <tr key={r.name}><td>{r.name}</td><td className="num">{fmtN(r.calls)} calls</td><td className="num">{fmtN(r.runs)} runs</td><td className="num">{fmtMs(r.total_ms)}</td></tr>)}</tbody></table>
              </Panel>
              <Panel title="Slowest calls">
                <table className="table"><tbody>{detail.data.slowest.map((s, i) => <tr key={i}><td><RunLink id={s.run_id} /></td><td className="small">{s.run_name}</td><td className="num">{fmtMs(s.duration_ms)}</td><td className="small error-text">{s.error}</td></tr>)}</tbody></table>
              </Panel>
            </div>
          )}
        </State>
      )}
      <Panel title="Top statements by total time" wide>
        <table className="table">
          <thead><tr><th>Statement</th><th>Table</th><th className="num">Calls</th><th className="num">Runs</th><th className="num">Mean</th><th className="num">p95</th><th className="num">Total</th></tr></thead>
          <tbody>{q.data?.statements.map((s) => (
            <tr key={s.statement}><td className="mono small">{s.operation} · {s.statement}</td><td>{s.tbl ?? "—"}</td><td className="num">{fmtN(s.calls)}</td><td className="num">{fmtN(s.runs)}</td>
              <td className="num">{fmtMs(Number(s.mean_ms))}</td><td className="num">{fmtMs(Number(s.p95))}</td><td className="num">{fmtMs(Number(s.total_ms))}</td></tr>))}</tbody>
        </table>
        <p className="muted small">Statements are hashed after removing every value; the SQL text and its parameters are never stored.</p>
      </Panel>
    </State>
  );
}

interface Efficiency {
  prices: string; turns: number; orders: number; llm_calls: number; tokens: number; tokens_per_order: number | null; llm_calls_per_order: number | null;
  tokens_per_turn: number | null; retry_waste_ms: number; retry_waste_share: number | null;
  llm: { model: string; purpose: string; outcome: string; calls: number; tokens_in: number; tokens_out: number; tokens_cached: number; mean_latency_ms: number; p95_latency_ms: number }[];
  series: { t: string; tokens_in: number; tokens_out: number; calls: number }[];
}

export function OpsEfficiency() {
  const { range, params } = useOpsWindow();
  const keep = useKeep();
  const q = useOps<Efficiency>("/efficiency", params);
  const d = q.data;
  return (
    <State q={q}>
      {d && (
        <>
          <div className="banner dark">Money isn't tracked yet (no prices until GCP). Tokens and calls are the cost proxy.</div>
          <div className="tiles">
            <Tile label="Tokens per order" value={fmtN(d.tokens_per_order)} sub={<>{fmtN(d.tokens)} tokens · {fmtN(d.orders)} orders</>} />
            <Tile label="Model calls per order" value={d.llm_calls_per_order ?? "—"} sub="target 2–5 (architecture)" />
            <Tile label="Tokens per turn" value={fmtN(d.tokens_per_turn)} sub={<>{fmtN(d.turns)} turns</>} />
            <Tile label="Retry waste" value={fmtPct(d.retry_waste_share)} sub={<>{fmtMs(d.retry_waste_ms)} on attempts that failed</>}
              to={keep("runs", { name: "turn", outcome: "RETRYING,FAILED,ABANDONED" })} tone={(d.retry_waste_share ?? 0) > 0.05 ? "warning" : undefined} />
          </div>
          <div className="panels">
            <Panel title="Tokens per interval">
              <StackedBars spanS={RANGES[range]} keys={[{ key: "in", label: "Input tokens", color: COLOR.p50 }, { key: "out", label: "Output tokens", color: COLOR.p95 }]}
                rows={d.series.map((s) => ({ t: Date.parse(s.t), in: Number(s.tokens_in ?? 0), out: Number(s.tokens_out ?? 0) }))} />
            </Panel>
            <Panel title="Model calls by model, purpose and outcome">
              <table className="table">
                <thead><tr><th>Model · purpose</th><th>Outcome</th><th className="num">Calls</th><th className="num">In / out tokens</th><th className="num">Mean · p95</th></tr></thead>
                <tbody>{d.llm.map((r, i) => (
                  <tr key={i}><td>{r.model}<div className="muted small">{r.purpose}</div></td>
                    <td>{r.outcome === "ok" ? <span className="chip ok">ok</span> : <span className="chip warning">{r.outcome}</span>}</td>
                    <td className="num">{fmtN(r.calls)}</td><td className="num">{fmtN(r.tokens_in)} / {fmtN(r.tokens_out)}</td>
                    <td className="num">{fmtMs(Number(r.mean_latency_ms))} · {fmtMs(Number(r.p95_latency_ms))}</td></tr>))}</tbody>
              </table>
            </Panel>
          </div>
        </>
      )}
    </State>
  );
}
