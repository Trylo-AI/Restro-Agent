// Live (PDS page 2; Q1): what is running now, the work queue, held leases, ambiguous sends, process heartbeats.
// Refreshes every 5 s.
import { Link } from "react-router-dom";

import { useOps } from "../../lib/ops";
import { fmtMs, fmtN } from "./charts";
import { Panel, RunLink, State, Tile, useKeep } from "./common";

interface Live {
  running: { run_id: string; service: string; name: string; subject_type: string | null; subject_id: string | null; current_stage: string | null; attempt: number; elapsed_ms: number; p95: number | null; slow: boolean | null }[];
  queue: { due: number; waiting: number; running: number; dead_letters_24h: number; oldest_due: string | null };
  leases_held: number; ambiguous_sends: number;
  instances: { instance: string; service: string; git_sha: string | null; last_seen: string; stale: boolean }[];
  last_hour: { requeued: number; abandoned: number; failed: number };
}

export function OpsLive() {
  const q = useOps<Live>("/live", {}, { refetchMs: 5000 });
  const keep = useKeep();
  const d = q.data;
  return (
    <State q={q}>
      {d && (
        <>
          <div className="tiles">
            <Tile label="Running now" value={fmtN(d.running.length)} sub={<>{d.running.filter((r) => r.slow).length} past 3× their p95</>} tone={d.running.some((r) => r.slow) ? "warning" : undefined} />
            <Tile label="Turns due, not started" value={fmtN(d.queue.due)} tone={d.queue.due > 3 ? "warning" : undefined} sub={<>{fmtN(d.queue.waiting)} waiting on debounce or retry</>} />
            <Tile label="Conversation leases held" value={fmtN(d.leases_held)} />
            <Tile label="Ambiguous sends" value={fmtN(d.ambiguous_sends)} tone={d.ambiguous_sends ? "warning" : undefined} sub="resolved by send_check" />
            <Tile label="Dead letters, 24 h" value={fmtN(d.queue.dead_letters_24h)} tone={d.queue.dead_letters_24h ? "critical" : undefined} to={keep("runs", { name: "turn", outcome: "FAILED,ABANDONED", range: "24h" })} />
            <Tile label="Last hour" value={<>{fmtN(d.last_hour.requeued)} retried</>} sub={<>{fmtN(d.last_hour.abandoned)} abandoned · {fmtN(d.last_hour.failed)} failed</>} to={keep("runs", { outcome: "RETRYING", range: "1h" })} />
          </div>
          <div className="panels">
            <Panel title="Running" wide>
              {d.running.length ? (
                <table className="table">
                  <thead><tr><th>Run</th><th>Name</th><th>Subject</th><th>Stage</th><th className="num">Elapsed</th><th className="num">p95 (7 days)</th></tr></thead>
                  <tbody>{d.running.map((r) => (
                    <tr key={r.run_id} className={r.slow ? "slow" : ""}>
                      <td><RunLink id={r.run_id} /></td><td>{r.name}<div className="muted small">{r.service} · attempt {r.attempt}</div></td>
                      <td className="small mono">{r.subject_type ? `${r.subject_type}:${(r.subject_id ?? "").slice(0, 12)}` : "—"}</td>
                      <td>{r.current_stage ?? "—"}</td>
                      <td className="num">{fmtMs(r.elapsed_ms)}{r.slow && <span className="chip warning">slow</span>}</td>
                      <td className="num">{fmtMs(r.p95)}</td>
                    </tr>))}</tbody>
                </table>
              ) : <div className="viz-empty">Nothing running right now.</div>}
            </Panel>
            <Panel title="Processes" wide>
              <table className="table">
                <thead><tr><th>Instance</th><th>Service</th><th>Git</th><th>Last heartbeat</th></tr></thead>
                <tbody>{d.instances.map((i) => (
                  <tr key={i.instance}><td className="mono small">{i.instance}</td><td>{i.service}</td><td className="mono">{i.git_sha?.slice(0, 7)}</td>
                    <td>{i.stale ? <span className="chip critical">stale</span> : <span className="chip ok">alive</span>} {new Date(i.last_seen).toLocaleTimeString("en-IN")}</td></tr>))}</tbody>
              </table>
              <p className="muted small">A process that stops heart-beating for 60 s has its running runs marked Abandoned. <Link to={keep("runs", { outcome: "ABANDONED" })}>See them →</Link></p>
            </Panel>
          </div>
        </>
      )}
    </State>
  );
}
