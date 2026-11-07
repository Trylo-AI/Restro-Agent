// One order's full history (`/audit/orders/:orderId`, docs/console.md#audit-log). Owner only. Customer, staff and
// automatic events, escalations, the bill and the code, merged by the API and shown oldest first.
import { BadgeCheck, Clock, LifeBuoy, Sparkles, UserRound, type LucideIcon } from "lucide-react";
import { Link, Navigate, useParams } from "react-router-dom";

import { Empty } from "../../components/ui";
import { ApiError } from "../../lib/api/client";
import { useOrderHistory, type HistoryEvent, type OrderHistory } from "../../lib/audit";
import { clock, dayMonth, inr } from "../../lib/format";
import { atLeast, useSession } from "../../lib/session";
import { SeverityMark } from "./AuditRow";
import "../../styles/audit.css";

const STATUS: Record<string, string> = { NEW: "New", ACCEPTED: "Accepted", READY: "Ready", SERVED: "Served", CANCELLED: "Cancelled" };
const SOURCE: Record<string, string> = { qr: "WhatsApp (table QR)", campaign: "WhatsApp (campaign)", manual: "Manual order" };
const REASON: Record<string, string> = {
  out_of_stock: "Out of stock", kitchen_issue: "Kitchen issue", customer_asked: "Customer asked", other: "Other",
};
const ACTOR: Record<HistoryEvent["actor"]["type"], { icon: LucideIcon; label: string }> = {
  customer: { icon: UserRound, label: "Customer" },
  staff: { icon: BadgeCheck, label: "Staff" },
  support: { icon: LifeBuoy, label: "Restro support" },
  ai: { icon: Sparkles, label: "AI" },
  system: { icon: Clock, label: "Automatic" },
};

export function OrderHistoryScreen() {
  const { role } = useSession();
  if (!atLeast(role, "owner")) return <Navigate to="/orders" replace />;
  return <OrderTimeline />;
}

function OrderTimeline() {
  const { orderId } = useParams();
  const q = useOrderHistory(orderId ?? null);
  if (q.isPending) return <Empty>Loading the order's history…</Empty>;
  if (q.isError) {
    const e = q.error;
    if (e instanceof ApiError && e.status === 404) return <Empty>Order not found in this café. <Link to="/audit">Back to the audit log</Link></Empty>;
    if (e instanceof ApiError && e.status === 403) return <Empty>Only the owner can see the audit log.</Empty>;
    return <Empty>Couldn't load this order's history. <button className="btn small" onClick={() => void q.refetch()}>Try again</button></Empty>;
  }
  const h = q.data;
  const events = [...h.events].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  return (
    <div className="audit audit-history">
      <Header o={h.order} />
      {h.order.status === "CANCELLED" && <Cancelled o={h.order} />}
      <section aria-label="Timeline">
        {events.length ? (
          <ol className="audit-timeline">
            {events.map((ev, i) => <Event key={ev.audit_id ?? `${ev.at}-${i}`} ev={ev} />)}
          </ol>
        ) : <Empty>Nothing recorded for this order yet.</Empty>}
      </section>
      <div className="row wrap audit-history-foot">
        <Link className="btn small" to={`/audit?order_id=${h.order.id}&range=all`}>All actions on this order in the audit log</Link>
        {h.order.customer?.id && <Link className="btn small ghost" to={`/customers/${h.order.customer.id}`}>Open customer</Link>}
      </div>
    </div>
  );
}

function Header({ o }: { o: OrderHistory["order"] }) {
  const parts = [`Order #${o.number}`, o.table, o.customer ? o.customer.name ?? "Customer" : "Guest", STATUS[o.status] ?? o.status];
  return (
    <header className="audit-history-head">
      <h2>{parts.filter(Boolean).join(" · ")}</h2>
      <p className="muted">{inr(o.total_paise)} · {SOURCE[o.source] ?? o.source} · placed {dayMonth(o.created_at)}, {clock(o.created_at)}</p>
    </header>
  );
}

function Cancelled({ o }: { o: OrderHistory["order"] }) {
  // Reason code `customer` = the customer cancelled on WhatsApp: no staff name and no staff reason.
  const byCustomer = o.cancel_reason_code === "customer";
  const reason = o.cancel_reason_code && !byCustomer ? REASON[o.cancel_reason_code] ?? o.cancel_reason_code : null;
  const by = byCustomer ? " by the customer" : o.cancelled_by_name ? ` by ${o.cancelled_by_name}` : "";
  return (
    <div className="audit-cancelled" role="note">
      <strong>Cancelled{by}{reason ? ` — ${reason}` : ""}</strong>
      {o.cancel_reason_text && (
        <div className="audit-staff-note">
          <div className="muted small">Staff note (never sent to the customer)</div>
          <div>{o.cancel_reason_text}</div>
        </div>
      )}
    </div>
  );
}

function Event({ ev }: { ev: HistoryEvent }) {
  const a = ACTOR[ev.actor.type] ?? ACTOR.system;
  return (
    <li className={`audit-event sev-${ev.severity}`}>
      <span className="audit-event-icon" title={a.label}><a.icon size={16} aria-label={a.label} /></span>
      <div className="audit-main">
        <div className="audit-sentence">
          <time className="audit-time mono" dateTime={ev.at}>{clock(ev.at)}</time> <SeverityMark severity={ev.severity} /> {ev.sentence}
        </div>
        {ev.severity !== "info" && ev.severity_reason && <div className="audit-reason">{ev.severity_reason}</div>}
        {!!ev.details?.length && (
          <dl className="facts small">
            {ev.details.map((d, i) => [<dt key={`t${i}`}>{d.label}</dt>, <dd key={`d${i}`}>{d.value}</dd>])}
          </dl>
        )}
      </div>
    </li>
  );
}
