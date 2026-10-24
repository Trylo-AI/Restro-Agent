import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { Alerts, Debug } from "../lib/types";

const FAULTS: { id: string; label: string; hint: string }[] = [
  { id: "send_timeout", label: "Next send times out", hint: "→ ambiguous; send_check resolves it; never sent twice" },
  { id: "send_429", label: "Next send gets 429", hint: "→ retried with backoff, sent once" },
  { id: "llm_fail", label: "Next LLM call fails", hint: "→ retry on the other model" },
  { id: "llm_invalid", label: "Next LLM output invalid", hint: "→ retry on the other model" },
  { id: "turn_crash", label: "Next turn crashes", hint: "→ retried 2 / 4 / 8 s; 4th crash dead-letters" },
];

const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export function Panel({ waId }: { waId: string | null }) {
  const [debug, setDebug] = useState<Debug | null>(null);
  const [alerts, setAlerts] = useState<Alerts | null>(null);
  const [armed, setArmed] = useState<Record<string, string[]>>({});
  const [tab, setTab] = useState<"state" | "llm" | "log" | "alerts">("state");

  useEffect(() => {
    if (!waId) return;
    let alive = true;
    const tick = async () => {
      try {
        const [d, a, f] = await Promise.all([api.debug(waId), api.alerts(), api.faults()]);
        if (alive) {
          setDebug(d);
          setAlerts(a);
          setArmed(f);
        }
      } catch {
        /* the phone shows the error */
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1500);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [waId]);

  const convo = debug?.conversation;
  const state = convo?.state;
  const armedList = Object.values(armed).flat();

  return (
    <aside className="panel">
      <h2>Behind the scenes</h2>
      <p className="muted">Phone {waId ? `· ${waId.slice(-4)}` : ""}</p>
      <nav className="tabs">
        {(["state", "llm", "log", "alerts"] as const).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {{ state: "State", llm: "LLM", log: "Event log", alerts: "Alert phone" }[t]}
          </button>
        ))}
      </nav>

      {tab === "state" && (
        <div>
          {!convo && <p className="muted">No conversation yet.</p>}
          {convo && (
            <dl className="facts">
              <dt>Step</dt><dd>{state?.step ?? "—"}</dd>
              <dt>Mode</dt><dd className={convo.mode === "human" ? "warn" : ""}>{convo.mode}{convo.assigned_to ? " (assigned)" : ""}</dd>
              <dt>Awaiting</dt><dd>{state?.awaiting ?? "—"}</dd>
              <dt>Table</dt><dd>{convo.table ?? "—"}</dd>
              <dt>Strikes</dt><dd>{state?.clarify_strikes ?? 0} / 2</dd>
              <dt>Allergy</dt><dd>{state?.allergy_note ?? "—"}</dd>
              <dt>Lease</dt><dd>{convo.lease.owner ? `held until ${fmtTime(convo.lease.expires_at ?? "")}` : "free"}</dd>
            </dl>
          )}
          <h3>Cart {state?.cart ? `· v${state.cart.version}` : ""}</h3>
          {state?.cart?.items.length ? (
            <ul className="lines">
              {state.cart.items.map((l, i) => (
                <li key={i}>{l.qty} × {l.name ?? l.item_id.slice(0, 8)}{l.note ? ` — ${l.note}` : ""}</li>
              ))}
            </ul>
          ) : <p className="muted">Empty</p>}
          <h3>Work queue</h3>
          {debug?.work?.length ? (
            <ul className="lines">
              {debug.work.map((w, i) => (
                <li key={i} className={w.status === "dead_lettered" ? "crit" : ""}>
                  {w.status} · attempts {w.attempts}{w.last_error ? ` · ${w.last_error}` : ""}
                </li>
              ))}
            </ul>
          ) : <p className="muted">Nothing queued</p>}
          <h3>Order events</h3>
          {debug?.order_events?.length ? (
            <ul className="lines">
              {debug.order_events.map((e, i) => (
                <li key={i}>#{e.number} {e.from_status ?? "—"} → {e.to_status} · {e.actor_type}{e.reason ? ` · ${e.reason}` : ""}</li>
              ))}
            </ul>
          ) : <p className="muted">No orders yet</p>}
          <h3>Raw state</h3>
          <Json value={state ?? {}} />
        </div>
      )}

      {tab === "llm" && (
        <div>
          {!debug?.llm_calls?.length && <p className="muted">No LLM calls yet. Buttons never call the LLM.</p>}
          {debug?.llm_calls?.map((c) => {
            const trace = debug.traces?.find((t) => t.llm_call_id === c.id);
            return (
              <div key={c.id} className="card">
                <div className="card-head">
                  <strong>{c.purpose}</strong> · {c.model} · <span className={c.outcome === "ok" ? "" : "crit"}>{c.outcome}</span>
                </div>
                <div className="muted">
                  {c.latency_ms} ms · in {c.input_tokens} (cached {c.cached_tokens}) · out {c.output_tokens} · {Number(c.cost).toFixed(5)} {c.currency}
                </div>
                {trace && (
                  <>
                    <h4>Parsed</h4>
                    <Json value={trace.parsed} />
                    <h4>Guardrails</h4>
                    <Json value={trace.guardrails} />
                    <details><summary>Raw output</summary><pre className="json">{trace.raw_output}</pre></details>
                    <details><summary>Prompt suffix ({trace.prompt_chars} chars total)</summary><pre className="json">{trace.suffix}</pre></details>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      {tab === "log" && (
        <ul className="log">
          {[...(debug?.events ?? [])].reverse().map((e, i) => {
            const { at, kind, ...rest } = e;
            return (
              <li key={i}>
                <span className="mono">{fmtTime(at)}</span> <strong>{kind}</strong>{" "}
                <span className="muted">{Object.entries(rest).map(([k, v]) => `${k}=${String(v)}`).join(" ")}</span>
              </li>
            );
          })}
        </ul>
      )}

      {tab === "alerts" && (
        <div>
          <p className="muted">new_order_alert sends to the café alert number and managers' alert phones.</p>
          {!alerts?.alerts.length && <p className="muted">No alerts sent.</p>}
          {alerts?.alerts.map((a) => (
            <div key={a.id} className="card">
              <div className="card-head"><strong>{a.template_name}</strong> · {a.status} · {fmtTime(a.created_at)}</div>
              <div className="muted">to {a.payload._to_ref}</div>
              <div>{a.payload.template?.components[0]?.parameters.map((p) => p.text).join(" · ")}</div>
            </div>
          ))}
        </div>
      )}

      <h3>Faults</h3>
      <p className="muted">Each fault fires once, on the next matching event.</p>
      {FAULTS.map((f) => (
        <button key={f.id} className={`fault${armedList.includes(f.id) ? " armed" : ""}`} title={f.hint}
          onClick={async () => setArmed(await api.armFault(f.id))}>
          {armedList.includes(f.id) ? "● " : ""}{f.label}
        </button>
      ))}
    </aside>
  );
}
