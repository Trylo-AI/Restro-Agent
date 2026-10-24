// One live order card (docs/console.md#live-orders).
import { CircleAlert, MessageCircle, Printer, Timer, UserRound, X } from "lucide-react";

import { Flag, Progress, SeverityChip } from "../../components/ui";
import type { LiveOrder } from "../../lib/data";
import { clock, inr, minutesSince, ordinal, secondsSince } from "../../lib/format";
import type { Urgency } from "../../lib/urgency";
import { NEXT } from "./useOrderActions";

const FLAG_LABEL: Record<string, string> = {
  edited: "Edited", item_sold_out: "Item sold out", near_closing: "Near closing", escalated: "Escalated",
};

export interface CardActions {
  advance(): void;
  delay(): void;
  cancel(): void;
  message(): void;
  print(): void;
  profile(): void;
}

function visitLine(o: LiveOrder): string {
  if (!o.customer_id) return o.source === "manual" ? "Manual order" : "Guest";
  const name = o.customerName ?? "Guest";
  return `${name} · ${o.visits > 1 ? `${ordinal(o.visits)} visit` : "First visit"}`;
}

function waited(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return `${m}:${String(seconds % 60).padStart(2, "0")}`;
}

export function OrderCard({ o, urgency, now, canCancel, offline, busy, act }: {
  o: LiveOrder; urgency: Urgency; now: number; canCancel: boolean; offline: boolean; busy: boolean; act: CardActions;
}) {
  const next = NEXT[o.status];
  const budget = o.expected_minutes + o.delay_minutes;
  const elapsed = minutesSince(o.accepted_at, now);
  const showAllergy = o.status !== "READY" && (!!o.allergy_note || o.profileAllergies.length > 0);
  const flags = o.flags.filter((f) => FLAG_LABEL[f] && f !== "escalated");
  const writeOff = offline || busy;

  return (
    <article className={`order-card sev-${urgency.severity}`} aria-label={`Order ${o.number}, table ${o.table ?? "unknown"}`}>
      {showAllergy && (
        <div className={`allergy-banner${o.allergy_note ? "" : " profile"}`}>
          <CircleAlert size={18} aria-hidden />
          <div>
            {o.allergy_note && <div><strong>Allergy:</strong> {o.allergy_note}</div>}
            {o.profileAllergies.length > 0 && <div><strong>On profile:</strong> {o.profileAllergies.join(", ")}</div>}
          </div>
        </div>
      )}
      <div className="card-head">
        <div className="table-label">{o.table ?? "—"}</div>
        <div className="grow">
          <div className="who"><span className="mono">#{o.number}</span> · {visitLine(o)}</div>
          <div className="row chips">
            <SeverityChip severity={urgency.severity} label={urgency.chip} />
            {urgency.severity === "none" && urgency.chip && <span className="chip flag">{urgency.chip}</span>}
            {flags.map((f) => <Flag key={f}>{FLAG_LABEL[f]}</Flag>)}
            {o.source === "manual" && <Flag>Manual</Flag>}
          </div>
        </div>
        {o.customer_id && (
          <button className="btn small" onClick={act.profile} aria-label={`Profile of ${o.customerName ?? "guest"}`}>
            <UserRound size={15} /> Profile
          </button>
        )}
      </div>

      {o.status === "ACCEPTED" && (
        <div className="timer">
          <div className="row"><Timer size={15} aria-hidden /> <strong>{elapsed} / {budget} min</strong>
            {o.delay_minutes > 0 && <span className="muted">incl. +{o.delay_minutes} delay</span>}</div>
          <Progress value={budget ? elapsed / budget : 0} severity={urgency.severity} />
        </div>
      )}
      {o.status === "NEW" && (
        <div className="timer row"><Timer size={15} aria-hidden /> Waiting {waited(secondsSince(o.created_at, now))}</div>
      )}

      <ul className="items">
        {o.items.map((it) => (
          <li key={it.id}>
            <span className="qty">{it.qty} ×</span> {it.name_snapshot}
            {it.note && <div className="note">{it.note}</div>}
          </li>
        ))}
      </ul>

      {o.escalations.length > 0 && (
        <div className="chain">
          Escalated · {o.escalations.map((e) => `${e.target === "owner" ? "Owner" : "Managers"} ${clock(e.sent_at)}`).join(" · ")}
        </div>
      )}

      <div className="total row"><span className="muted">Total (incl. GST)</span><span className="grow" /><strong>{inr(o.total_paise)}</strong></div>

      <div className="card-actions">
        <div className="row">
          {o.customer_id && (
            <button className="btn small" onClick={act.message} disabled={writeOff}><MessageCircle size={15} /> Message</button>
          )}
          <button className="btn small" onClick={act.print}><Printer size={15} /> Print</button>
          {(o.status === "NEW" || o.status === "ACCEPTED") && (
            <button className="btn small" onClick={act.delay} disabled={writeOff}>Delay</button>
          )}
          {canCancel && (
            <button className="btn small danger" onClick={act.cancel} disabled={writeOff}><X size={15} /> Cancel</button>
          )}
        </div>
        {next && (
          <button className="btn primary big" onClick={act.advance} disabled={writeOff}>{next.label}</button>
        )}
      </div>
    </article>
  );
}
