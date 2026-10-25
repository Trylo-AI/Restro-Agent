// Runs explorer (PDS page 5) and Run trace (page 6; Q12, Q13): find the runs where …, then see one run step by
// step on its own time axis, every attempt, its events and the runs it started.
import { ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { useOps, useOpsWindow, type RunRow, type Span } from "../../lib/ops";
import { fmtMs, fmtN } from "./charts";
import { Attrs, OutcomeChip, Panel, RunLink, State, useKeep } from "./common";

const FILTERS = ["kind", "name", "outcome", "subject", "tenant", "fingerprint", "reason", "min_ms", "q", "label.turn_kind"] as const;

export function OpsRuns() {
  const { params } = useOpsWindow();
  const [sp, setSp] = useSearchParams();
  const [pages, setPages] = useState<string[]>([]);
  const filters = Object.fromEntries(FILTERS.map((k) => [k, sp.get(k) ?? undefined]));
  const before = pages[pages.length - 1];
  const q = useOps<{ runs: RunRow[]; next: string | null }>("/runs", { ...params, ...filters, before, limit: 50 });

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(sp);
    if (v) next.set(k, v);
    else next.delete(k);
    setSp(next, { replace: true });
    setPages([]);
  };

  return (
    <>
      <div className="ops-filter-row" role="group" aria-label="Filter runs">
        <select aria-label="Kind" value={sp.get("kind") ?? ""} onChange={(e) => set("kind", e.target.value)}>
          <option value="">every kind</option>{["job", "message", "http", "cron"].map((k) => <option key={k}>{k}</option>)}
        </select>
        <input aria-label="Name" placeholder="name (turn, POST /orders/{order_id}/…)" value={sp.get("name") ?? ""} onChange={(e) => set("name", e.target.value)} />
        <select aria-label="Outcome" value={sp.get("outcome") ?? ""} onChange={(e) => set("outcome", e.target.value)}>
          <option value="">every outcome</option>
          {["SUCCEEDED", "SUCCEEDED_DEGRADED", "RETRYING", "FAILED,ABANDONED", "REJECTED", "SUPERSEDED", "RUNNING"].map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <input aria-label="Slower than (ms)" placeholder="slower than ms" inputMode="numeric" value={sp.get("min_ms") ?? ""} onChange={(e) => set("min_ms", e.target.value.replace(/\D/g, ""))} />
        <input aria-label="Search" placeholder="run id, subject id" value={sp.get("q") ?? ""} onChange={(e) => set("q", e.target.value)} />
        {FILTERS.filter((k) => !["kind", "name", "outcome", "min_ms", "q"].includes(k) && sp.get(k)).map((k) => (
          <button key={k} className="chip-btn on" onClick={() => set(k, "")}>{k} = {sp.get(k)} ×</button>
        ))}
      </div>
      <State q={q} empty={!q.data?.runs.length}>
        <table className="table runs-table">
          <thead><tr><th>Started</th><th>Run</th><th>Name</th><th>Subject</th><th>Attempt</th><th>Outcome</th><th className="num">Work</th><th className="num">Wait</th><th>Stage / error</th></tr></thead>
          <tbody>
            {q.data?.runs.map((r) => (
              <tr key={r.run_id}>
                <td className="small">{new Date(r.started_at).toLocaleTimeString("en-IN")}</td>
                <td><RunLink id={r.run_id} /></td>
                <td>{r.name}<div className="muted small">{r.service} · {r.kind}{r.labels.turn_kind ? ` · ${r.labels.turn_kind}` : ""}</div></td>
                <td className="small mono">{r.subject_type ? `${r.subject_type}:${(r.subject_id ?? "").slice(0, 12)}` : "—"}</td>
                <td className="num">{r.attempt}</td>
                <td><OutcomeChip outcome={r.outcome} />{r.degradations ? <span className="muted small"> {r.degradations} fallback{r.degradations > 1 ? "s" : ""}</span> : null}</td>
                <td className="num">{fmtMs(r.duration_ms)}</td>
                <td className="num">{fmtMs(r.queue_wait_ms)}</td>
                <td className="small">{r.error_code ? `${r.error_stage ?? "?"} · ${r.error_code}` : r.outcome === "RUNNING" ? r.current_stage : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row pager">
          {pages.length > 0 && <button className="btn small" onClick={() => setPages(pages.slice(0, -1))}>Newer</button>}
          {q.data?.next && <button className="btn small" onClick={() => setPages([...pages, q.data!.next!])}>Older</button>}
        </div>
      </State>
    </>
  );
}

interface Detail {
  run: RunRow & { finished_at: string | null; instance: string | null; git_sha: string | null; config_hash: string | null; failure_reason: string | null;
    error_type: string | null; degradations: { reason: string; at_ms: number; span: string | null }[]; stage_ms: Record<string, number>;
    dep_ms: Record<string, number>; dep_calls: Record<string, number>; input_size: number | null; input_size_unit: string | null; queued_at: string | null;
    upstream_trace: string | null; bytes_out: number | null };
  spans: Span[];
  events: { seq: number; at_ms: number; level: string; span_name: string | null; logger: string | null; message: string; detail: Record<string, unknown> | null }[];
  attempts: { run_id: string; attempt: number; started_at: string; duration_ms: number | null; outcome: string; error_stage: string | null; error_code: string | null; failure_reason: string | null }[];
  stage_p50: Record<string, number | null>;
  upstream: { run_id?: string; name?: string; service?: string | null; trace?: string } | null;
  downstream: { run_id: string; name: string; service: string; outcome: string; duration_ms: number | null }[];
}

type Node = Span & { children: Node[]; repeated?: number };

/** Builds the span tree; ≥ 3 consecutive siblings with the same name and target collapse into one ×n row. */
export function tree(spans: Span[]): Node[] {
  const byId = new Map<number, Node>(spans.map((s) => [s.span_id, { ...s, children: [] }]));
  const roots: Node[] = [];
  for (const n of byId.values()) {
    const p = n.parent_id !== null ? byId.get(n.parent_id) : undefined;
    (p ? p.children : roots).push(n);
  }
  const collapse = (list: Node[]): Node[] => {
    list.sort((a, b) => a.start_ms - b.start_ms);
    const out: Node[] = [];
    for (const n of list) {
      n.children = collapse(n.children);
      const prev = out[out.length - 1];
      if (prev && !prev.children.length && !n.children.length && prev.name === n.name && prev.target === n.target && n.level === 3) {
        prev.repeated = (prev.repeated ?? 1) + 1;
        prev.attrs = { ...prev.attrs, _ms: [...((prev.attrs._ms as number[] | undefined) ?? [prev.duration_ms]), n.duration_ms] };
        prev.duration_ms = n.start_ms + n.duration_ms - prev.start_ms;
        if (n.outcome !== "ok") prev.outcome = n.outcome;
        continue;
      }
      out.push(n);
    }
    return out;
  };
  return collapse(roots);
}

function WaterfallRow({ n, total, depth, p50, open, toggle, pick, picked }: {
  n: Node; total: number; depth: number; p50: Record<string, number | null>; open: Set<number>; toggle: (id: number) => void;
  pick: (n: Node) => void; picked: number | null;
}) {
  const left = (n.start_ms / total) * 100;
  const width = Math.max(0.3, (n.duration_ms / total) * 100);
  const ratio = n.level === 1 && p50[n.name] ? n.duration_ms / (p50[n.name] ?? 1) : null;
  const isOpen = open.has(n.span_id);
  return (
    <>
      <div className={`wf-row lvl-${n.level}${picked === n.span_id ? " picked" : ""}`} role="row">
        <div className="wf-name" style={{ paddingLeft: depth * 14 }}>
          {n.children.length ? (
            <button className="wf-toggle" aria-label={isOpen ? "Collapse" : "Expand"} onClick={() => toggle(n.span_id)}>
              {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>
          ) : <span className="wf-toggle" />}
          <button className="link" onClick={() => pick(n)}>{n.name}{n.repeated ? ` ×${n.repeated}` : ""}</button>
          {n.target && <span className="muted small"> {n.target}</span>}
        </div>
        <div className="wf-track">
          <span className={`wf-bar k-${n.kind} o-${n.outcome}`} style={{ left: `${left}%`, width: `${width}%` }} />
        </div>
        <div className="wf-ms">
          {fmtMs(n.duration_ms)}
          {n.outcome !== "ok" && <span className={`chip ${n.outcome === "failed" ? "critical" : "warning"}`}>{n.outcome}</span>}
          {ratio !== null && ratio >= 1.5 && <span className="chip warning" title="vs this stage's 7-day p50">{ratio.toFixed(1)}× p50</span>}
        </div>
      </div>
      {isOpen && n.children.map((c) => <WaterfallRow key={c.span_id} n={c} total={total} depth={depth + 1} p50={p50} open={open} toggle={toggle} pick={pick} picked={picked} />)}
    </>
  );
}

export function OpsRunTrace() {
  const { runId = "" } = useParams();
  const keep = useKeep();
  const q = useOps<Detail>(`/runs/${runId}`);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [picked, setPicked] = useState<Node | null>(null);
  const nodes = useMemo(() => tree(q.data?.spans ?? []), [q.data]);
  const d = q.data;
  const total = Math.max(d?.run.duration_ms ?? 0, ...(d?.spans ?? []).map((s) => s.start_ms + s.duration_ms), 1);
  const toggle = (id: number) => setOpen((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const expandAll = () => setOpen(new Set((d?.spans ?? []).map((s) => s.span_id)));

  return (
    <State q={q}>
      {d && (
        <div className="trace">
          <header className="trace-head">
            <div>
              <h2>{d.run.name}{d.run.subject_type ? <span className="muted"> · {d.run.subject_type} {d.run.subject_id?.slice(0, 12)}</span> : null}</h2>
              <div className="muted small">
                attempt {d.run.attempt}{d.attempts.length > 1 ? ` of ${d.attempts.length}` : ""} · {d.run.service} · {d.run.instance?.slice(0, 28)} · git {d.run.git_sha?.slice(0, 7) ?? "?"} · config {d.run.config_hash?.slice(0, 6)}
                {d.run.input_size !== null ? ` · input ${d.run.input_size} ${d.run.input_size_unit}` : ""}
                {Object.entries(d.run.dep_calls).map(([k, v]) => ` · ${v} ${k}`).join("")}
              </div>
            </div>
            <div className="trace-outcome"><OutcomeChip outcome={d.run.outcome} /><strong>{fmtMs(d.run.duration_ms)}</strong>
              {d.run.queue_wait_ms !== null && <span className="muted small">waited {fmtMs(d.run.queue_wait_ms)} first</span>}
              {d.run.counts.reply_ms !== undefined && <span className="muted small">reply {fmtMs(d.run.counts.reply_ms)}</span>}</div>
          </header>
          {d.run.failure_reason && <div className="banner critical">{d.run.error_stage ?? "?"} · {d.run.error_code} · {d.run.failure_reason}</div>}
          {(d.upstream || d.downstream.length > 0) && (
            <div className="trace-links small">
              {d.upstream && (d.upstream.run_id ? <>Started by <RunLink id={d.upstream.run_id}>{d.upstream.name}</RunLink> ({d.upstream.service})</> : <>Started by trace {d.upstream.trace?.slice(0, 8)} (not traced here)</>)}
              {d.downstream.length > 0 && <> · Started {d.downstream.map((x, i) => <span key={x.run_id}>{i ? ", " : " "}<RunLink id={x.run_id}>{x.name}</RunLink> <OutcomeChip outcome={x.outcome} /></span>)}</>}
            </div>
          )}

          <Panel title="Waterfall" wide right={<span className="row"><button className="btn small" onClick={expandAll}>Expand all</button><button className="btn small" onClick={() => setOpen(new Set())}>Collapse</button></span>}>
            <div className="wf" role="table" aria-label="Spans on the run's time axis">
              <div className="wf-row wf-axis" role="row"><div className="wf-name" /><div className="wf-track">
                {[0, 0.25, 0.5, 0.75, 1].map((f) => <span key={f} style={{ left: `${f * 100}%` }}>{fmtMs(total * f)}</span>)}</div><div className="wf-ms" /></div>
              {d.run.queue_wait_ms ? <div className="wf-row" role="row"><div className="wf-name muted">waiting before start</div><div className="wf-track"><span className="wf-bar wait" style={{ left: 0, width: "100%" }} /></div><div className="wf-ms">{fmtMs(d.run.queue_wait_ms)}</div></div> : null}
              {nodes.map((n) => <WaterfallRow key={n.span_id} n={n} total={total} depth={0} p50={d.stage_p50} open={open} toggle={toggle} pick={setPicked} picked={picked?.span_id ?? null} />)}
            </div>
            <div className="wf-legend muted small">Bars: <i className="k-internal" /> stage / step <i className="k-db" /> Postgres <i className="k-llm" /> Gemini <i className="k-http_out" /> WhatsApp <i className="k-queue" /> queue · red outline = failed</div>
            {picked && (
              <div className="span-detail">
                <strong>{picked.name}{picked.repeated ? ` ×${picked.repeated}` : ""}</strong> · {picked.kind} · level {picked.level} · starts at {fmtMs(picked.start_ms)} · {fmtMs(picked.duration_ms)}
                {picked.repeated && Array.isArray(picked.attrs._ms) && (() => { const ms = [...(picked.attrs._ms as number[])].sort((a, b) => a - b); return <div className="small">min {fmtMs(ms[0])} · median {fmtMs(ms[Math.floor(ms.length / 2)])} · max {fmtMs(ms[ms.length - 1])}</div>; })()}
                {picked.error && <div className="error-text">{picked.error}</div>}
                <Attrs attrs={Object.fromEntries(Object.entries(picked.attrs).filter(([k]) => k !== "_ms"))} />
              </div>
            )}
          </Panel>

          <div className="panels">
            <Panel title={`Events (${d.events.length})`}>
              {d.events.length ? (
                <table className="table"><tbody>{d.events.map((e) => (
                  <tr key={e.seq}><td className="small num">{fmtMs(e.at_ms)}</td><td><span className={`chip ${e.level === "ERROR" ? "critical" : "warning"}`}>{e.level}</span></td>
                    <td>{e.message}<div className="muted small">{e.span_name}{e.logger ? ` · ${e.logger}` : ""}</div>{e.detail && <Attrs attrs={e.detail} />}</td></tr>))}
                </tbody></table>
              ) : <div className="viz-empty">No warnings, errors or fallbacks during this run.</div>}
              <Link className="small" to={keep("logs", { run_id: d.run.run_id })}>Logs for this run →</Link>
            </Panel>
            <Panel title={`Attempts (${d.attempts.length || 1})`}>
              {d.attempts.length ? (
                <table className="table"><tbody>{d.attempts.map((a) => (
                  <tr key={a.run_id} className={a.run_id === d.run.run_id ? "current" : ""}>
                    <td>#{a.attempt}</td><td><RunLink id={a.run_id} /></td><td><OutcomeChip outcome={a.outcome} /></td><td className="num">{fmtMs(a.duration_ms)}</td>
                    <td className="small">{a.error_code ? `${a.error_stage ?? "?"} · ${a.error_code} · ${a.failure_reason ?? ""}` : ""}</td></tr>))}
                </tbody></table>
              ) : <div className="viz-empty">One attempt; no subject to group by.</div>}
              <Link className="small" to={keep("runs", { name: d.run.name, outcome: d.run.outcome })}>Similar runs →</Link>
            </Panel>
            <Panel title="Stages against their 7-day p50">
              <table className="table"><thead><tr><th>Stage</th><th className="num">This run</th><th className="num">p50 (7 days)</th></tr></thead>
                <tbody>{Object.entries(d.run.stage_ms).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
                  <tr key={k}><td><Link to={keep("latency", { level: "1", name: k })}>{k}</Link></td><td className="num">{fmtMs(v)}</td><td className="num">{fmtMs(d.stage_p50[k])}</td></tr>))}
                </tbody></table>
              <div className="muted small">Fallbacks: {d.run.degradations.length ? d.run.degradations.map((x) => x.reason).join(", ") : "none"} · {fmtN(d.spans.length)} spans</div>
            </Panel>
          </div>
        </div>
      )}
    </State>
  );
}
