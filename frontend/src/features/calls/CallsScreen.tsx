// Calls and bills: help, allergy questions, bill requests, low ratings, and the bill sheet (docs/console.md#calls-and-bills).
import { useQueryClient } from "@tanstack/react-query";
import { MessageCircle, Receipt, UserRound } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useNow } from "../../app/alerts";
import { useOffline } from "../../app/App";
import { useToasts } from "../../app/toasts";
import { Dialog, Empty, SeverityChip } from "../../components/ui";
import { useBill, useOpenRequests, type LiveRequest } from "../../lib/data";
import { clock, inr, secondsSince } from "../../lib/format";
import { bills, conversations, requests } from "../../lib/mutations";
import { atLeast, useSession } from "../../lib/session";
import { rank, requestUrgency, type Urgency } from "../../lib/urgency";
import { CustomerProfilePanel } from "../customer-profile/CustomerProfilePanel";

const FILTERS = ["All", "Waiting too long", "Allergy", "Low rating", "Needs help", "Bill", "With staff"] as const;
type Filter = (typeof FILTERS)[number];
type Row = { r: LiveRequest; u: Urgency };

const KIND: Record<LiveRequest["kind"], string> = { help: "Needs help", allergy: "Allergy question", bill: "Bill", low_rating: "Low rating" };

function matches({ r, u }: Row, f: Filter): boolean {
  if (f === "All") return true;
  if (f === "With staff") return r.status === "acknowledged";
  if (f === "Allergy") return r.kind === "allergy";
  if (f === "Low rating") return r.kind === "low_rating";
  if (f === "Bill") return r.kind === "bill";
  return u.chip === f;
}

function waiting(s: number): string {
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}

export function CallsScreen() {
  const list = useOpenRequests();
  const now = useNow();
  const offline = useOffline();
  const toasts = useToasts();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { role, cafeId } = useSession();
  const [filter, setFilter] = useState<Filter>("All");
  const [bill, setBill] = useState<LiveRequest | null>(null);
  const [profileId, setProfileId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const manager = atLeast(role, "manager");

  const rows = useMemo<Row[]>(() => (list.data ?? [])
    .filter((r) => r.kind !== "low_rating" || manager)
    .map((r) => ({ r, u: requestUrgency(r, now, r.ackName) }))
    .sort((a, b) => rank(b.u.severity) - rank(a.u.severity) || Date.parse(a.r.created_at) - Date.parse(b.r.created_at)),
  [list.data, now, manager]);
  const shown = rows.filter((x) => matches(x, filter));

  const summary = [
    [rows.filter((x) => x.u.chip === "Waiting too long").length, "waiting too long"],
    [rows.filter((x) => x.r.kind === "allergy" && x.r.status === "open").length, "allergy"],
    [rows.filter((x) => x.r.kind === "help" && x.r.status === "open").length, "need help"],
    [rows.filter((x) => x.r.kind === "bill").length, "bills"],
  ].filter(([n]) => (n as number) > 0).map(([n, l]) => `${n} ${l}`);

  async function act(r: LiveRequest, fn: () => Promise<unknown>, done?: string) {
    setBusy(r.id);
    try {
      await fn();
      if (done) toasts.show(done);
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Something went wrong. Please try again.", true);
    } finally {
      setBusy(null);
      void qc.invalidateQueries({ queryKey: ["requests", cafeId] });
    }
  }

  const openChat = (r: LiveRequest) => {
    if (r.conversation_id) navigate(`/chats/${r.conversation_id}`);
    else if (r.table_session_id) void act(r, async () => navigate(`/chats/${(await conversations.open(r.table_session_id!)).id}`));
  };

  const buttons = (r: LiveRequest) => {
    const off = offline || busy === r.id;
    const message = <button className="btn small" disabled={off} onClick={() => openChat(r)}><MessageCircle size={15} /> Message</button>;
    if (r.kind === "bill") {
      return <>{message}<button className="btn primary" disabled={off} onClick={() => setBill(r)}><Receipt size={15} /> Open bill</button></>;
    }
    if (r.kind === "low_rating") {
      return <>
        <button className="btn small" disabled={off} onClick={() => openChat(r)}><MessageCircle size={15} /> Open chat</button>
        <button className="btn primary" disabled={off} onClick={() => void act(r, () => requests.resolve(r.id), "Marked handled")}>Mark handled</button>
      </>;
    }
    if (r.status === "open") {
      return <>
        {r.kind === "allergy"
          ? <button className="btn small" disabled={off} onClick={() => openChat(r)}><MessageCircle size={15} /> Reply</button>
          : message}
        <button className="btn primary" disabled={off} onClick={() => void act(r, () => requests.ack(r.id), "Acknowledged · the alarm stops for everyone")}>Acknowledge</button>
      </>;
    }
    return <>{message}<button className="btn primary" disabled={off}
      onClick={() => void act(r, () => requests.resolve(r.id), r.kind === "help" ? "Resolved · the bot is back on this chat" : "Resolved")}>Resolve</button></>;
  };

  return (
    <div className="orders">
      <div className="summary" aria-live="polite">{summary.length ? summary.join(" · ") : "No one is waiting"}</div>
      <div className="filters row" role="group" aria-label="Filter">
        {FILTERS.filter((f) => f !== "Low rating" || manager).map((f) => (
          <button key={f} className={`chip-btn${filter === f ? " on" : ""}`} aria-pressed={filter === f} onClick={() => setFilter(f)}>{f}</button>
        ))}
      </div>

      {list.isPending && <Empty>Loading calls…</Empty>}
      {list.isError && !list.data && <Empty>Couldn't load calls. <button className="btn small" onClick={() => void list.refetch()}>Try again</button></Empty>}
      {list.data && (shown.length ? (
        <div className="card-grid">
          {shown.map(({ r, u }) => (
            <article key={r.id} className={`order-card call-card sev-${u.severity}`} aria-label={`${KIND[r.kind]}, table ${r.table ?? "unknown"}`}>
              <div className="card-head">
                <div className="table-label">{r.table ?? "—"}</div>
                <div className="grow">
                  <div className="who">{KIND[r.kind]} · {r.customerName ?? "Guest"}</div>
                  <div className="row chips"><SeverityChip severity={u.severity} label={u.chip} /></div>
                </div>
                {r.customerId && (
                  <button className="btn small" onClick={() => setProfileId(r.customerId)} aria-label={`Profile of ${r.customerName ?? "guest"}`}>
                    <UserRound size={15} /> Profile
                  </button>
                )}
              </div>
              {r.words && r.kind !== "bill" && <div className="words">“{r.words}”</div>}
              <div className="muted">
                {r.status === "acknowledged"
                  ? `Acknowledged by ${r.ackName ?? "a teammate"} · ${clock(r.acknowledged_at)}`
                  : `Waiting ${waiting(secondsSince(r.created_at, now))}`}
              </div>
              {r.escalations.length > 0 && (
                <div className="chain">Escalated · {r.escalations.map((e) => `${e.target === "owner" ? "Owner" : "Managers"} ${clock(e.sent_at)}`).join(" · ")}</div>
              )}
              <div className="card-actions"><div className="row">{buttons(r)}</div></div>
            </article>
          ))}
        </div>
      ) : <Empty>{filter === "All" ? "No calls or bills right now." : `Nothing matches “${filter}”.`}</Empty>)}

      {bill?.table_session_id && (
        <BillSheet request={bill} offline={offline} onClose={() => setBill(null)}
          onDelivered={() => void qc.invalidateQueries({ queryKey: ["requests", cafeId] })} />
      )}
      {profileId && <CustomerProfilePanel customerId={profileId} onClose={() => setProfileId(null)} />}
    </div>
  );
}

function BillSheet({ request, offline, onClose, onDelivered }: {
  request: LiveRequest; offline: boolean; onClose: () => void; onDelivered: () => void;
}) {
  const bill = useBill(request.table_session_id);
  const toasts = useToasts();
  const qc = useQueryClient();
  const { cafeId } = useSession();
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const orders = bill.data?.orders ?? [];

  async function apply() {
    setBusy(true);
    setCodeError(null);
    try {
      const out = await bills.applyCode(request.table_session_id!, code.trim());
      qc.setQueryData(["bill", cafeId, request.table_session_id], out);
      toasts.show(`Code applied · ${inr(out.discount_paise ?? 0)} off`);
      setCode("");
    } catch (e) {
      setCodeError(e instanceof Error ? e.message : "Couldn't apply the code");
    } finally {
      setBusy(false);
    }
  }

  async function deliver() {
    setBusy(true);
    try {
      const out = await bills.deliver(request.table_session_id!);
      toasts.show(`Bill delivered · ${request.table ?? "Table"} closed · ${inr(out.total_paise)}`);
      onDelivered();
      onClose();
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't close the bill", true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Bill · ${request.table ?? ""}`} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Close</button>
        <button className="btn primary" disabled={busy || offline || bill.isPending} onClick={() => void deliver()}>Bill delivered</button>
      </>
    }>
      {bill.isPending ? <p className="muted">Loading…</p> : orders.length ? (
        <>
          <ul className="plist">
            {orders.map((o) => (
              <li key={o.id} className="row">
                <span className="grow"><span className="mono">#{o.number}</span> · {(o.items ?? []).map((i) => `${i.qty} × ${i.name_snapshot}`).join(", ")}</span>
                <strong>{inr(o.total_paise ?? 0)}</strong>
              </li>
            ))}
          </ul>
          {bill.data?.code ? (
            <>
              <div className="row bill-line"><span>Subtotal</span><span className="grow" /><span>{inr(bill.data.subtotal_paise ?? 0)}</span></div>
              <div className="row bill-line"><span>Offer code {bill.data.code}</span><span className="grow" /><span>− {inr(bill.data.discount_paise ?? 0)}</span></div>
            </>
          ) : (
            <form className="row code-row" onSubmit={(e) => { e.preventDefault(); if (code.trim().length >= 3) void apply(); }}>
              <label className="field grow">Offer code<input value={code} maxLength={20} autoCapitalize="characters" onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="BACK20-7KQ" /></label>
              <button className="btn" disabled={busy || offline || code.trim().length < 3}>Apply</button>
            </form>
          )}
          {codeError && <div className="error-text">{codeError}</div>}
          <div className="total row bill-total"><span>Total (incl. GST)</span><span className="grow" /><strong>{inr(bill.data?.total_paise ?? 0)}</strong></div>
        </>
      ) : <p className="muted">No orders on this table yet.</p>}
      <p className="hint">"Bill delivered" closes the table. The next scan at this table starts a new bill.</p>
    </Dialog>
  );
}
