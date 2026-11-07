// One audit log row (docs/console.md#audit-log). Severity is colour + icon + label, never colour alone, and never a
// pulse: pulse means "act now" in the urgency engine and history isn't urgent (D26).
import { ChevronDown, CircleAlert, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import type { AuditEntry } from "../../lib/audit";
import { clock } from "../../lib/format";
import type { AuditFilterState, Severity } from "./filters";
import "../../styles/audit.css";

const ALIAS: Record<string, string> = { ACCEPTED: "order.accept", READY: "order.ready", SERVED: "order.serve" };

/** The filter-only action (D28a) for an accept / ready / serve row, so "Only this action" finds who accepted. */
export function filterAction(e: Pick<AuditEntry, "action" | "change">): string {
  const to = (e.change as Record<string, unknown>).to;
  return e.action === "order.transition" && typeof to === "string" && ALIAS[to] ? ALIAS[to]! : e.action;
}

export function SeverityMark({ severity }: { severity: Severity }) {
  if (severity === "critical") return <span className="audit-sev critical"><CircleAlert size={15} aria-hidden /> Critical</span>;
  if (severity === "warning") return <span className="audit-sev warning"><TriangleAlert size={15} aria-hidden /> Warning</span>;
  return null;
}

/** The sentence with the actor's name in bold when the sentence starts with it ("Priya (Shift manager) cancelled…"). */
function Sentence({ e }: { e: AuditEntry }) {
  const name = e.actor.name;
  if (name && e.sentence.startsWith(name)) return <span><strong>{name}</strong>{e.sentence.slice(name.length)}</span>;
  return <span>{e.sentence}</span>;
}

export function AuditRow({ e, categoryLabel, roleLabel, onFilter }: {
  e: AuditEntry;
  categoryLabel: string;
  roleLabel: string;
  onFilter: (patch: Partial<AuditFilterState>) => void;
}) {
  const [open, setOpen] = useState(false);
  const panel = `audit-row-${e.id}`;
  return (
    <li className={`audit-row sev-${e.severity}`}>
      <div className="audit-line">
        <time className="audit-time mono" dateTime={e.at}>{clock(e.at)}</time>
        <div className="audit-main">
          <div className="audit-sentence"><SeverityMark severity={e.severity} /> <Sentence e={e} /></div>
          {e.severity !== "info" && e.severity_reason && <div className="audit-reason">{e.severity_reason}</div>}
          <div className="audit-links">
            <span className="chip calm">{categoryLabel}</span>
            {e.order && <Link className="chip-link mono" to={`/audit/orders/${e.order.id}`}>#{e.order.number}</Link>}
            {e.customer && <Link className="chip-link" to={`/customers/${e.customer.id}`}>{e.customer.name ?? "Customer"}</Link>}
            {e.conversation_id && <Link className="chip-link" to={`/chats/${e.conversation_id}`}>Chat</Link>}
            {e.links?.campaign_id && <Link className="chip-link" to={`/campaigns/${e.links.campaign_id}`}>Campaign</Link>}
          </div>
        </div>
        <button className="btn ghost small audit-expand" aria-expanded={open} aria-controls={panel}
          aria-label={open ? "Hide details" : "Show details"} onClick={() => setOpen(!open)}>
          <ChevronDown size={18} aria-hidden />
        </button>
      </div>
      {open && (
        <div className="audit-more" id={panel}>
          {e.details.length > 0 && (
            <dl className="facts">
              {e.details.map((d, i) => [<dt key={`t${i}`}>{d.label}</dt>, <dd key={`d${i}`}>{d.value}</dd>])}
            </dl>
          )}
          <div className="row wrap">
            {e.order && <Link className="btn small" to={`/audit/orders/${e.order.id}`}>Order history</Link>}
            {e.conversation_id && <Link className="btn small" to={`/chats/${e.conversation_id}`}>Open chat</Link>}
            {e.customer && <Link className="btn small" to={`/customers/${e.customer.id}`}>Open customer</Link>}
            <button className="btn small ghost" onClick={() => onFilter(e.actor.user_id
              ? { actor: [e.actor.user_id], role: [] } : { actor: [], role: [e.actor.role] })}>Only this person</button>
            {e.order && <button className="btn small ghost" onClick={() => onFilter({ order_id: e.order!.id, order: null })}>Only this order</button>}
            <button className="btn small ghost" onClick={() => onFilter({ action: [filterAction(e)] })}>Only this action</button>
          </div>
          <details className="audit-tech">
            <summary>Technical details</summary>
            <dl className="facts small">
              <dt>Action</dt><dd className="mono">{e.action}</dd>
              <dt>Role</dt><dd>{roleLabel} <span className="mono muted">{e.actor.role}</span></dd>
              <dt>Support</dt><dd>{e.actor.support ? "Yes" : "No"}</dd>
              <dt>Entry</dt><dd className="mono">{e.id}</dd>
              {e.actor.user_id && <><dt>User</dt><dd className="mono">{e.actor.user_id}</dd></>}
              {e.order && <><dt>Order</dt><dd className="mono">{e.order.id}</dd></>}
              {e.customer && <><dt>Customer</dt><dd className="mono">{e.customer.id}</dd></>}
              {e.conversation_id && <><dt>Chat</dt><dd className="mono">{e.conversation_id}</dd></>}
            </dl>
            <pre className="audit-json">{JSON.stringify(e.change, null, 2)}</pre>
          </details>
        </div>
      )}
    </li>
  );
}
