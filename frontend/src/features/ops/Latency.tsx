// Latency (PDS page 8; Q4, Q5, Q6): the percentile table per run or span name, a drill-down (histogram, trend,
// slowest runs, breakdown) and Compare: two cohorts span by span with a significance flag.
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { RANGES, useOps, useOpsWindow, type Pct } from "../../lib/ops";
import { COLOR, fmtMs, fmtN, fmtPct, Histogram, TimeChart } from "./charts";
import { Panel, RunLink, State } from "./common";

const LEVELS = [["0", "Runs"], ["1", "Stages"], ["2", "Steps"], ["3", "Calls"]] as const;
const GROUPS = [["", "no breakdown"], ["git_sha", "git commit"], ["config_hash", "config"], ["label.turn_kind", "turn kind"], ["label.llm_model", "model"], ["instance", "process"], ["tenant", "café"], ["service", "service"]] as const;

interface Row { name: string; kind?: string; n: number; p50: number | null; p90: number | null; p95: number | null; p99: number | null; max: number | null; share: number | null }
interface Detail {
  summary: Pct; histogram: { le: number | null; n: number }[]; trend: ({ t: string } & Pct)[];
  slowest: { run_id: string; started_at: string; ms: number }[]; breakdown: ({ group: string } & Pct)[];
}
interface Compare { spans: { span: string; a: Pct; b: Pct; delta_p50: number | null; delta_pct: number | null; p_value: number | null; significant: boolean }[] }

export function OpsLatency() {
  const { range, params } = useOpsWindow();
  const [sp, setSp] = useSearchParams();
  const level = sp.get("level") ?? "0";
  const name = sp.get("name") ?? "";
  const groupBy = sp.get("group_by") ?? "";
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n, { replace: true }); };
  const table = useOps<{ rows: Row[] }>("/latency", { ...params, level });
  const detail = useOps<Detail>("/latency", { ...params, level, name, group_by: groupBy || undefined }, { enabled: !!name });

  return (
    <>
      <div className="ops-filter-row">
        <div className="segmented" role="radiogroup" aria-label="Level">
          {LEVELS.map(([v, l]) => <button key={v} role="radio" aria-checked={level === v} className={level === v ? "on" : ""} onClick={() => { const n = new URLSearchParams(sp); n.set("level", v); n.delete("name"); setSp(n, { replace: true }); }}>{l}</button>)}
        </div>
      </div>
      <State q={table} empty={!table.data?.rows.length}>
        <Panel title="Percentiles: the optimisation backlog in one table" wide>
          <table className="table">
            <thead><tr><th>Name</th><th className="num">Count</th><th className="num">p50</th><th className="num">p90</th><th className="num">p95</th><th className="num">p99</th><th className="num">Max</th><th className="num">Share of work</th></tr></thead>
            <tbody>{table.data?.rows.map((r) => (
              <tr key={r.name} className={r.name === name ? "current" : ""}>
                <td><button className="link" onClick={() => set("name", r.name)}>{r.name}</button>{r.kind ? <span className="muted small"> {r.kind}</span> : null}</td>
                <td className="num">{fmtN(r.n)}</td><td className="num">{fmtMs(r.p50)}</td><td className="num">{fmtMs(r.p90)}</td>
                <td className="num">{fmtMs(r.p95)}</td><td className="num">{fmtMs(r.p99)}</td><td className="num">{fmtMs(r.max)}</td><td className="num">{fmtPct(r.share)}</td>
              </tr>))}</tbody>
          </table>
          <p className="muted small">Runs: final successful runs. Spans: every span of that level. Share can pass 100 % for nested spans; stages are top-level only.</p>
        </Panel>
      </State>
      {name && (
        <State q={detail}>
          {detail.data && (
            <>
              <h2 className="section-title">{name} <span className="muted">· {fmtN(detail.data.summary.n)} · p50 {fmtMs(detail.data.summary.p50)} · p95 {fmtMs(detail.data.summary.p95)}</span></h2>
              <div className="panels">
                <Panel title="Distribution">
                  <Histogram buckets={detail.data.histogram} />
                </Panel>
                <Panel title="p50 and p95 over time">
                  <TimeChart spanS={RANGES[range]} lines={[
                    { key: "p50", label: "p50", color: COLOR.p50, points: detail.data.trend.map((t) => ({ t: Date.parse(t.t), v: t.p50 })) },
                    { key: "p95", label: "p95", color: COLOR.p95, points: detail.data.trend.map((t) => ({ t: Date.parse(t.t), v: t.p95 })) },
                  ]} />
                </Panel>
                <Panel title="Slowest 20">
                  <table className="table"><tbody>{detail.data.slowest.map((s) => (
                    <tr key={s.run_id + s.ms}><td><RunLink id={s.run_id} /></td><td className="small">{new Date(s.started_at).toLocaleString("en-IN")}</td><td className="num">{fmtMs(s.ms)}</td></tr>))}</tbody></table>
                </Panel>
                <Panel title="Breakdown" right={
                  <select aria-label="Break down by" value={groupBy} onChange={(e) => set("group_by", e.target.value)}>
                    {GROUPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>}>
                  {detail.data.breakdown.length ? (
                    <table className="table"><thead><tr><th>Value</th><th className="num">Count</th><th className="num">p50</th><th className="num">p95</th></tr></thead>
                      <tbody>{detail.data.breakdown.map((b) => <tr key={b.group}><td className="mono small">{b.group.slice(0, 24)}</td><td className="num">{fmtN(b.n)}</td><td className="num">{fmtMs(b.p50)}</td><td className="num">{fmtMs(b.p95)}</td></tr>)}</tbody></table>
                  ) : <div className="viz-empty">Pick a breakdown.</div>}
                </Panel>
              </div>
              {level === "0" && <CompareBox name={name} />}
            </>
          )}
        </State>
      )}
    </>
  );
}

function CompareBox({ name }: { name: string }) {
  const { params } = useOpsWindow();
  const [key, setKey] = useState("git_sha");
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const values = useOps<{ values: { value: string; n: number; first: string }[] }>("/latency/cohorts", { ...params, name, key });
  const cmp = useOps<Compare>("/latency/compare", { ...params, name, key, a, b }, { enabled: !!a && !!b && a !== b });
  const opts = values.data?.values ?? [];
  return (
    <Panel title="Compare two cohorts: prove an optimisation" wide right={
      <span className="row">
        <select aria-label="Cohort key" value={key} onChange={(e) => { setKey(e.target.value); setA(""); setB(""); }}>
          {GROUPS.filter(([v]) => v).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select aria-label="Cohort A" value={a} onChange={(e) => setA(e.target.value)}><option value="">A…</option>{opts.map((o) => <option key={o.value} value={o.value}>{o.value.slice(0, 12)} ({o.n})</option>)}</select>
        <select aria-label="Cohort B" value={b} onChange={(e) => setB(e.target.value)}><option value="">B…</option>{opts.map((o) => <option key={o.value} value={o.value}>{o.value.slice(0, 12)} ({o.n})</option>)}</select>
      </span>}>
      {!a || !b ? <div className="viz-empty">{opts.length < 2 ? "Only one cohort in this range: widen the range, or deploy a change." : "Pick cohort A and B."}</div> : (
        <State q={cmp}>
          <table className="table">
            <thead><tr><th>Span</th><th className="num">A p50 (n)</th><th className="num">B p50 (n)</th><th className="num">Δ p50</th><th className="num">A p95</th><th className="num">B p95</th><th>Significant?</th></tr></thead>
            <tbody>{cmp.data?.spans.map((s) => (
              <tr key={s.span}>
                <td>{s.span}</td><td className="num">{fmtMs(s.a.p50)} ({s.a.n})</td><td className="num">{fmtMs(s.b.p50)} ({s.b.n})</td>
                <td className={`num ${s.significant ? ((s.delta_p50 ?? 0) > 0 ? "delta-bad" : "delta-good") : ""}`}>{s.delta_p50 === null ? "—" : `${s.delta_p50 > 0 ? "+" : ""}${fmtMs(s.delta_p50)}`}{s.delta_pct !== null ? ` (${s.delta_pct > 0 ? "+" : ""}${s.delta_pct} %)` : ""}</td>
                <td className="num">{fmtMs(s.a.p95)}</td><td className="num">{fmtMs(s.b.p95)}</td>
                <td>{s.significant ? <span className="chip info">yes · p {s.p_value?.toFixed(3)}</span> : <span className="muted small">no{s.p_value !== null ? ` · p ${s.p_value.toFixed(2)}` : " · too few runs"}</span>}</td>
              </tr>))}</tbody>
          </table>
          <p className="muted small">Significant = Mann-Whitney p &lt; 0.05 with ≥ 10 runs each side.</p>
        </State>
      )}
    </Panel>
  );
}
