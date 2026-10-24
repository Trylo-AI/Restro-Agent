// Live orders: New → Accepted → Ready → Served (docs/console.md#live-orders).
import { useQueryClient } from "@tanstack/react-query";
import { CirclePause, CirclePlay, Columns3, Plus, Rows3 } from "lucide-react";
import { useMemo, useState } from "react";

import { useNow } from "../../app/alerts";
import { useOffline } from "../../app/App";
import { useToasts } from "../../app/toasts";
import { Empty, SeverityChip } from "../../components/ui";
import { useCafe, useLiveOrders, type LiveOrder } from "../../lib/data";
import { store, stored, usePhone } from "../../lib/media";
import { cafes } from "../../lib/mutations";
import { atLeast, useSession } from "../../lib/session";
import { orderUrgency, rank, worst, type Urgency } from "../../lib/urgency";
import { CustomerProfilePanel } from "../customer-profile/CustomerProfilePanel";
import { CancelDialog, DelayDialog, ManualOrderDialog } from "./dialogs";
import { OrderCard } from "./OrderCard";
import { printTicket } from "./ticket";
import { canCancel, useOrderActions } from "./useOrderActions";

type Column = "NEW" | "ACCEPTED" | "READY";
const COLUMNS: { status: Column; label: string }[] = [
  { status: "NEW", label: "New" }, { status: "ACCEPTED", label: "Accepted" }, { status: "READY", label: "Ready" },
];
const FILTERS = ["All", "Overdue", "Allergy", "Not started", "Due soon", "New", "Delayed"] as const;
type Filter = (typeof FILTERS)[number];

type Row = { o: LiveOrder; u: Urgency };

function matches(r: Row, f: Filter): boolean {
  if (f === "All") return true;
  if (f === "Allergy") return !!r.o.allergy_note && r.o.status !== "READY";
  if (f === "Delayed") return r.o.delay_minutes > 0 && r.o.status !== "READY";
  return r.u.chip === f;
}

const bySeverity = (a: Row, b: Row) =>
  rank(b.u.severity) - rank(a.u.severity) || Date.parse(a.o.created_at) - Date.parse(b.o.created_at);

type Open = { kind: "delay" | "cancel"; order: LiveOrder } | { kind: "manual" } | null;

export function OrdersScreen() {
  const orders = useLiveOrders();
  const cafe = useCafe();
  const now = useNow();
  const phone = usePhone();
  const offline = useOffline();
  const toasts = useToasts();
  const qc = useQueryClient();
  const { role, cafeId, cafeName } = useSession();
  const actions = useOrderActions();

  const [view, setView] = useState(() => stored("restro.ordersView", "tabs", ["tabs", "board"] as const));
  const [column, setColumn] = useState<Column>("NEW");
  const [filter, setFilter] = useState<Filter>("All");
  const [open, setOpen] = useState<Open>(null);
  const [profileId, setProfileId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const rows = useMemo<Row[]>(
    () => (orders.data ?? []).map((o) => ({ o, u: orderUrgency(o, now) })),
    [orders.data, now],
  );
  const latest = (o: LiveOrder) => orders.data?.find((x) => x.id === o.id) ?? o; // dialogs send the current version
  const shown = rows.filter((r) => matches(r, filter));
  const byColumn = (s: Column) => shown.filter((r) => r.o.status === s).sort(bySeverity);
  const board = view === "board" && !phone;

  const summary = [
    [rows.filter((r) => r.u.chip === "Overdue").length, "overdue"],
    [rows.filter((r) => r.o.allergy_note && r.o.status !== "READY").length, "allergy"],
    [rows.filter((r) => r.o.status === "NEW").length, "new"],
    [rows.filter((r) => r.u.chip === "Due soon").length, "due soon"],
  ].filter(([n]) => (n as number) > 0).map(([n, label]) => `${n} ${label}`);

  const paused = !!cafe.data?.settings?.paused;
  const canPause = atLeast(role, "manager");

  async function togglePause() {
    try {
      await cafes.pause(cafeId!, !paused);
      void qc.invalidateQueries({ queryKey: ["cafe", cafeId] });
      toasts.show(paused ? "WhatsApp ordering resumed" : "WhatsApp ordering paused · New QR scans get a polite message");
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't change ordering", true);
    }
  }

  async function guarded(o: LiveOrder, fn: () => Promise<unknown>) {
    setBusy(o.id);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  }

  const card = ({ o, u }: Row) => (
    <OrderCard
      key={o.id} o={o} urgency={u} now={now} offline={offline} busy={busy === o.id}
      canCancel={canCancel(o.status, role)}
      act={{
        advance: () => void guarded(o, () => actions.advance(o)),
        delay: () => setOpen({ kind: "delay", order: o }),
        cancel: () => setOpen({ kind: "cancel", order: o }),
        message: () => void guarded(o, () => actions.message(o)),
        print: () => printTicket(o, cafeName),
        profile: () => setProfileId(o.customer_id),
      }}
    />
  );

  const list = (items: Row[], emptyText: string) =>
    items.length ? <div className="card-grid">{items.map(card)}</div> : <Empty>{emptyText}</Empty>;

  const emptyText = (s: Column) => filter !== "All"
    ? `No ${COLUMNS.find((c) => c.status === s)!.label.toLowerCase()} orders match “${filter}”.`
    : s === "NEW" ? "No new orders. They appear here with a chime." : s === "ACCEPTED" ? "Nothing in the kitchen." : "Nothing waiting to be served.";

  return (
    <div className="orders">
      <div className="toolbar row">
        {!phone && (
          <div className="segmented" role="radiogroup" aria-label="Layout">
            <button role="radio" aria-checked={view === "tabs"} className={view === "tabs" ? "on" : ""}
              onClick={() => { setView("tabs"); store("restro.ordersView", "tabs"); }}><Rows3 size={15} /> Tabs</button>
            <button role="radio" aria-checked={view === "board"} className={view === "board" ? "on" : ""}
              onClick={() => { setView("board"); store("restro.ordersView", "board"); }}><Columns3 size={15} /> Board</button>
          </div>
        )}
        <span className="grow" />
        {canPause && (
          <button className={`btn small${paused ? " warn" : ""}`} onClick={() => void togglePause()} disabled={offline}>
            {paused ? <><CirclePlay size={15} /> Resume WhatsApp ordering</> : <><CirclePause size={15} /> Pause WhatsApp ordering</>}
          </button>
        )}
        <button className="btn small" onClick={() => setOpen({ kind: "manual" })}><Plus size={15} /> Add manual order</button>
      </div>

      <div className="summary" aria-live="polite">{summary.length ? summary.join(" · ") : "All calm"}</div>

      {!board && (
        <div className="col-tabs" role="tablist">
          {COLUMNS.map((c) => {
            const inCol = rows.filter((r) => r.o.status === c.status);
            const sev = worst(inCol.map((r) => r.u));
            return (
              <button key={c.status} role="tab" aria-selected={column === c.status} className={column === c.status ? "on" : ""}
                onClick={() => setColumn(c.status)}>
                {c.label} <span className="count">{inCol.length}</span>
                {(sev === "critical" || sev === "warning") && <span className={`dot ${sev}`} aria-label={sev} />}
              </button>
            );
          })}
        </div>
      )}

      <div className="filters row" role="group" aria-label="Filter">
        {FILTERS.map((f) => (
          <button key={f} className={`chip-btn${filter === f ? " on" : ""}`} aria-pressed={filter === f} onClick={() => setFilter(f)}>{f}</button>
        ))}
      </div>
      <div className="legend row" aria-label="Legend">
        <SeverityChip severity="critical" label="Act now" />
        <SeverityChip severity="warning" label="Soon" />
        <SeverityChip severity="info" label="New / delayed" />
      </div>

      {orders.isPending && <Empty>Loading orders…</Empty>}
      {orders.isError && !orders.data && (
        <Empty>Couldn't load orders. <button className="btn small" onClick={() => void orders.refetch()}>Try again</button></Empty>
      )}
      {orders.data && (board ? (
        <div className="board">
          {COLUMNS.map((c) => (
            <section key={c.status} className="board-col" aria-label={c.label}>
              <h2>{c.label} <span className="count">{byColumn(c.status).length}</span></h2>
              {list(byColumn(c.status), emptyText(c.status))}
            </section>
          ))}
        </div>
      ) : list(byColumn(column), emptyText(column)))}

      {open?.kind === "delay" && (
        <DelayDialog order={latest(open.order)} onClose={() => setOpen(null)}
          onSubmit={(m, n) => actions.delay(latest(open.order), m, n)} />
      )}
      {open?.kind === "cancel" && (
        <CancelDialog order={latest(open.order)} onClose={() => setOpen(null)}
          onSubmit={(r, t) => actions.cancel(latest(open.order), r, t)} />
      )}
      {open?.kind === "manual" && <ManualOrderDialog onClose={() => setOpen(null)} />}
      {profileId && <CustomerProfilePanel customerId={profileId} onClose={() => setProfileId(null)} />}
    </div>
  );
}
