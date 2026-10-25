// Overview (PDS page 1; Q1, Q2, Q4, Q7, Q8): is ordering healthy, how fast are replies against the budget,
// where does a turn's time go, what fails and which fallbacks fire.
import { Link, useNavigate } from "react-router-dom";

import { RANGES, useOps, useOpsWindow, type Finding, type Outcomes, type Pct } from "../../lib/ops";
import { COLOR, fmtMs, fmtN, fmtPct, ShareBars, StackedBars, TimeChart } from "./charts";
import { Delta, Panel, RunLink, State, Tile, useKeep } from "./common";

interface Overview {
  turns: Outcomes; turns_previous: Outcomes; all: Outcomes;
  reply_ms: Pct; reply_ms_previous: Pct; turn_work_ms: Pct; tap_reply_ms: Pct; webhook_ms: Pct;
  tokens_per_order: number | null; orders: number; llm_calls: number;
  series: { t: string; succeeded: number; degraded: number; failed: number; retrying: number; reply_p50: number | null; reply_p95: number | null; p50: number | null; p95: number | null }[];
  stage_share: { stage: string; ms: number; share: number }[];
  dependency_share: { kind: string; ms: number; share: number; calls: number }[];
  top_failures: { fingerprint: string; name: string; error_stage: string | null; error_code: string | null; sample: string | null; runs: number; units: number; last_seen: string }[];
  degradations: { reason: string; count: number; runs: number; cost_only: boolean | null; description: string | null }[];
  deploys: { at: string; service: string; git_sha: string | null }[];
  budget: { reply_p50_ms: number; reply_p95_ms: number; tap_reply_ms: number; webhook_ms: number };
}

const DEP_LABEL: Record<string, string> = { db: "Postgres", llm: "Gemini", http_out: "WhatsApp send", queue: "Queue" };

export function OpsOverview() {
  const { range, params } = useOpsWindow();
  const q = useOps<Overview>("/overview", params);
  const top = useOps<{ findings: Finding[] }>("/insights"); // 7 days, loaded on its own so tiles come first
  const insights = top.data?.findings.slice(0, 3) ?? [];
  const keep = useKeep();
  const navigate = useNavigate();
  const d = q.data;
  const spanS = RANGES[range];

  return (
    <State q={q}>
      {d && (
        <>
          <div className="tiles">
            <Tile label="Customer turns" value={fmtN(d.turns.units)} to={keep("runs", { name: "turn" })}
              sub={<>{fmtN(d.turns.retried)} retried · {fmtN(d.turns.recovered)} recovered</>} />
            <Tile label="Turn success" value={fmtPct(d.turns.success_rate)} tone={d.turns.failed ? "critical" : "ok"}
              to={keep("runs", { name: "turn", outcome: "FAILED,ABANDONED" })}
              sub={<>{fmtN(d.turns.failed)} failed for good</>} />
            <Tile label="Dead letters" value={fmtN(d.turns.failed)} tone={d.turns.failed ? "critical" : undefined}
              to={keep("failures")} sub={<Delta now={d.turns.failed} before={d.turns_previous.failed} />} />
            <Tile label="Fallbacks" value={fmtPct(d.turns.degraded_rate)} tone={d.turns.degraded ? "warning" : undefined}
              to={keep("failures", { tab: "degradations" })} sub={<>{d.degradations.filter((x) => !x.cost_only).length} reasons fired</>} />
            <Tile label="Reply time p50" value={fmtMs(d.reply_ms.p50)} tone={(d.reply_ms.p50 ?? 0) > d.budget.reply_p50_ms ? "critical" : "ok"}
              to={keep("latency", { name: "turn" })}
              sub={<>budget {fmtMs(d.budget.reply_p50_ms)} · <Delta now={d.reply_ms.p50} before={d.reply_ms_previous.p50} /></>} />
            <Tile label="Reply time p95" value={fmtMs(d.reply_ms.p95)} tone={(d.reply_ms.p95 ?? 0) > d.budget.reply_p95_ms ? "critical" : "ok"}
              to={keep("latency", { name: "turn" })}
              sub={<>budget {fmtMs(d.budget.reply_p95_ms)} · <Delta now={d.reply_ms.p95} before={d.reply_ms_previous.p95} /></>} />
            <Tile label="Button reply p95" value={fmtMs(d.tap_reply_ms.p95)} tone={(d.tap_reply_ms.p95 ?? 0) > d.budget.tap_reply_ms ? "critical" : "ok"}
              to={keep("runs", { name: "turn", "label.turn_kind": "tap" })} sub={<>budget {fmtMs(d.budget.tap_reply_ms)}</>} />
            <Tile label="Webhook p95" value={fmtMs(d.webhook_ms.p95)} tone={(d.webhook_ms.p95 ?? 0) > d.budget.webhook_ms ? "critical" : "ok"}
              to={keep("latency", { name: "POST /webhooks/whatsapp" })} sub={<>budget {fmtMs(d.budget.webhook_ms)}</>} />
            <Tile label="Tokens per order" value={fmtN(d.tokens_per_order)} to={keep("efficiency")}
              sub={<>{fmtN(d.llm_calls)} model calls · {fmtN(d.orders)} orders · no prices yet</>} />
          </div>
          <p className="muted small">Reply time: the customer's last message reaching us → our first reply sent. Work time p50 {fmtMs(d.turn_work_ms.p50)}, p95 {fmtMs(d.turn_work_ms.p95)}.</p>

          <div className="panels">
            <Panel title="Customer turns by final outcome">
              <StackedBars spanS={spanS} rows={d.series.map((s) => ({ t: Date.parse(s.t), ok: s.succeeded, degraded: s.degraded, failed: s.failed }))}
                keys={[{ key: "ok", label: "Succeeded", color: COLOR.ok }, { key: "degraded", label: "Degraded", color: COLOR.degraded }, { key: "failed", label: "Failed", color: COLOR.failed }]} />
            </Panel>
            <Panel title="Reply time against the budget">
              <TimeChart spanS={spanS}
                lines={[
                  { key: "p50", label: "p50", color: COLOR.p50, points: d.series.map((s) => ({ t: Date.parse(s.t), v: s.reply_p50 })) },
                  { key: "p95", label: "p95", color: COLOR.p95, points: d.series.map((s) => ({ t: Date.parse(s.t), v: s.reply_p95 })) },
                ]}
                thresholds={[{ v: d.budget.reply_p50_ms, label: "p50 budget 3 s" }, { v: d.budget.reply_p95_ms, label: "p95 budget 5 s" }]}
                markers={d.deploys.map((x) => ({ t: Date.parse(x.at), label: `deploy ${x.service} ${x.git_sha?.slice(0, 7) ?? ""}` }))} />
            </Panel>
            <Panel title="Where a turn's time goes: by stage">
              <ShareBars rows={d.stage_share.map((s) => ({ label: s.stage, share: Number(s.share), detail: fmtMs(Number(s.ms)) }))}
                onPick={(stage) => navigate(keep("latency", { level: "1", name: stage }))} />
            </Panel>
            <Panel title="Where a turn's time goes: by dependency">
              <ShareBars rows={d.dependency_share.map((s) => ({ key: s.kind, label: DEP_LABEL[s.kind] ?? s.kind, share: Number(s.share), detail: `${fmtN(s.calls)} calls` }))}
                onPick={() => navigate(keep("dependencies"))} />
            </Panel>
            <Panel title="Top failures" right={<Link to={keep("failures")} className="small">All failures →</Link>}>
              {d.top_failures.length ? (
                <table className="table">
                  <thead><tr><th>Where</th><th>Code</th><th className="num">Runs</th><th className="num">Units</th><th>Last seen</th></tr></thead>
                  <tbody>{d.top_failures.map((f) => (
                    <tr key={f.fingerprint}>
                      <td><Link to={keep("runs", { fingerprint: f.fingerprint })}>{f.name} · {f.error_stage ?? "—"}</Link><div className="muted small">{f.sample}</div></td>
                      <td>{f.error_code}</td><td className="num">{fmtN(f.runs)}</td><td className="num">{fmtN(f.units)}</td>
                      <td className="small">{new Date(f.last_seen).toLocaleString("en-IN")}</td>
                    </tr>))}</tbody>
                </table>
              ) : <div className="viz-empty">No failures in this range.</div>}
            </Panel>
            <Panel title="Silent fallbacks">
              {d.degradations.length ? (
                <table className="table">
                  <thead><tr><th>Reason</th><th className="num">Count</th><th className="num">Runs</th></tr></thead>
                  <tbody>{d.degradations.map((x) => (
                    <tr key={x.reason}>
                      <td><Link to={keep("runs", { reason: x.reason })}>{x.reason}</Link>{x.cost_only ? <span className="chip calm">cost only</span> : null}
                        <div className="muted small">{x.description}</div></td>
                      <td className="num">{fmtN(x.count)}</td><td className="num">{fmtN(x.runs)}</td>
                    </tr>))}</tbody>
                </table>
              ) : <div className="viz-empty">No fallbacks fired in this range.</div>}
            </Panel>
            <Panel title="Top insights" wide right={<Link to={keep("insights")} className="small">All insights →</Link>}>
              {top.isPending ? <div className="viz-empty">Loading…</div> : insights.length ? (
                <ul className="insight-list">{insights.map((f) => (
                  <li key={f.key}><span className={`chip ${f.severity === "high" ? "critical" : f.severity === "medium" ? "warning" : "calm"}`}>{f.severity}</span>
                    <Link to={keep("insights")}>{f.title}</Link>
                    {f.estimated_saving.ms_per_day ? <span className="muted small"> · saves ≈ {fmtMs(f.estimated_saving.ms_per_day)}/day</span> : null}
                    {f.example_run_ids[0] && <> · <RunLink id={f.example_run_ids[0]}>example</RunLink></>}
                  </li>))}</ul>
              ) : <div className="viz-empty">No findings in this range.</div>}
            </Panel>
          </div>
        </>
      )}
    </State>
  );
}
