// Customers (owner): list, profile and the read-only consent register (docs/console.md#customers).
// Numbers are masked everywhere, including the CSV. Delete needs the dialog and is audited.
import { useQueryClient } from "@tanstack/react-query";
import { Download, Trash2 } from "lucide-react";
import { useState } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { useToasts } from "../../app/toasts";
import { Dialog, Empty } from "../../components/ui";
import { PAGE, useConsentRegister, useCustomerList, useCustomerProfileRow, type CustomerProfileRow } from "../../lib/data";
import { dayMonth, inr } from "../../lib/format";
import { privacy } from "../../lib/mutations";
import { atLeast, useSession } from "../../lib/session";

const SEGMENTS = [[null, "All"], ["regular", "Regular"], ["new", "New"], ["lapsed", "Lapsed"]] as const;
const SEGMENT_LABEL: Record<string, string> = { regular: "Regular", new: "New", lapsed: "Lapsed" };
const CONSENT: Record<string, string> = { opted_in: "Opted in", opted_out: "Opted out", not_asked: "Not asked" };
const SOURCE: Record<string, string> = {
  feedback_flow: "After a meal", counter_qr: "Counter QR", order_flow: "While ordering", keyword: "Replied STOP",
  button: "Tapped Stop offers", meta_block: "Blocked on WhatsApp",
};

export function CustomersScreen() {
  const { role } = useSession();
  const { customerId } = useParams();
  const [sp, setSp] = useSearchParams();
  if (!atLeast(role, "owner")) return <Navigate to="/orders" replace />;
  const tab = sp.get("tab") === "consent" ? "consent" : "list";
  return (
    <div className="customers">
      <nav className="tabs-row" aria-label="Customers">
        <button className={`chip-btn${tab === "list" ? " on" : ""}`} onClick={() => setSp({})}>Customers</button>
        <button className={`chip-btn${tab === "consent" ? " on" : ""}`} onClick={() => setSp({ tab: "consent" })}>Consent register</button>
      </nav>
      {tab === "consent" ? <ConsentRegister /> : <CustomerList />}
      {customerId && <ProfileDialog customerId={customerId} />}
    </div>
  );
}

function CustomerList() {
  const [search, setSearch] = useState("");
  const [segment, setSegment] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const q = useCustomerList(search, segment, page);
  const navigate = useNavigate();
  const counts = q.data?.counts;
  return (
    <section>
      <div className="row toolbar wrap">
        <label className="field grow"><span className="sr-only">Search by name</span>
          <input type="search" placeholder="Search by name" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} /></label>
        <div className="row wrap" role="group" aria-label="Segment">
          {SEGMENTS.map(([s, label]) => (
            <button key={label} className={`chip-btn${segment === s ? " on" : ""}`} aria-pressed={segment === s} onClick={() => { setSegment(s); setPage(0); }}>{label}</button>
          ))}
        </div>
      </div>
      {counts && <p className="muted small">{counts.all} customers · {counts.regular} regular · {counts.new} new · {counts.lapsed} lapsed</p>}
      {q.isPending ? <Empty>Loading customers…</Empty> : q.isError ? <Empty>Couldn't load customers. <button className="btn small" onClick={() => void q.refetch()}>Try again</button></Empty>
        : !q.data.rows.length ? <Empty>{search || segment ? "No customers match." : "No customers yet."}</Empty> : (
          <>
            <table className="table customers-table">
              <thead><tr><th>Name</th><th>Phone</th><th>Segment</th><th className="num">Visits</th><th className="num">Spend</th><th>Last visit</th></tr></thead>
              <tbody>{q.data.rows.map((c) => (
                <tr key={c.customer_id} className="clickable" onClick={() => navigate(`/customers/${c.customer_id}`)}>
                  <td><button className="link">{c.name ?? "—"}</button></td>
                  <td className="mono small">{c.masked_phone}</td>
                  <td>{c.segment ? <span className="chip calm">{SEGMENT_LABEL[c.segment]}</span> : "—"}</td>
                  <td className="num">{c.visits}</td><td className="num">{inr(c.spend_paise)}</td>
                  <td>{c.last_visit_at ? dayMonth(c.last_visit_at) : "—"}</td>
                </tr>))}</tbody>
            </table>
            <div className="row pager">
              <span className="muted small grow">{page * PAGE + 1}–{Math.min((page + 1) * PAGE, q.data.total)} of {q.data.total}</span>
              <button className="btn small" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
              <button className="btn small" disabled={(page + 1) * PAGE >= q.data.total} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </>
        )}
    </section>
  );
}

function ProfileDialog({ customerId }: { customerId: string }) {
  const q = useCustomerProfileRow(customerId);
  const navigate = useNavigate();
  const close = () => navigate("/customers");
  const [confirming, setConfirming] = useState(false);
  const c = q.data;
  return (
    <Dialog title={c?.name ?? "Customer"} onClose={close} wide>
      {q.isPending ? <p className="muted">Loading…</p> : !c ? <p className="muted">This customer isn't available.</p> : (
        <>
          <div className="tiles">
            <div className="tile"><div className="tile-label">Visits</div><div className="tile-value">{c.visits}</div></div>
            <div className="tile"><div className="tile-label">Total spend</div><div className="tile-value">{inr(c.spend_paise)}</div><div className="tile-sub">cancelled orders excluded</div></div>
            <div className="tile"><div className="tile-label">Average rating</div><div className="tile-value">{c.avg_rating ? `${c.avg_rating}★` : "—"}</div><div className="tile-sub">{c.rating_count} ratings</div></div>
            <div className="tile"><div className="tile-label">Offers</div><div className="tile-value">{CONSENT[c.consent_status]}</div>
              <div className="tile-sub">{c.campaigns_received} received · {c.campaigns_redeemed} redeemed</div></div>
          </div>
          <dl className="facts">
            <dt>Phone</dt><dd className="mono">{c.masked_phone}</dd>
            <dt>First visit</dt><dd>{dayMonth(c.first_seen_at)}</dd>
            <dt>Last visit</dt><dd>{c.last_visit_at ? dayMonth(c.last_visit_at) : "—"}</dd>
            <dt>Segment</dt><dd>{c.segment ? SEGMENT_LABEL[c.segment] : "—"}</dd>
            <dt>Favourites</dt><dd>{c.top_items?.join(", ") || "—"}</dd>
          </dl>
          <button className="btn danger small" onClick={() => setConfirming(true)}><Trash2 size={15} /> Delete customer data</button>
          {confirming && <DeleteDialog customer={c} onClose={() => setConfirming(false)} onDone={close} />}
        </>
      )}
    </Dialog>
  );
}

function DeleteDialog({ customer, onClose, onDone }: { customer: CustomerProfileRow; onClose: () => void; onDone: () => void }) {
  const toasts = useToasts();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  return (
    <Dialog title="Delete this customer's data?" onClose={onClose} footer={
      <>
        <button className="btn primary" autoFocus onClick={onClose}>Keep data</button>
        <button className="btn danger" disabled={busy} onClick={async () => {
          setBusy(true);
          try {
            await privacy.deleteCustomer(customer.customer_id);
            toasts.show("Customer data deleted");
            void qc.invalidateQueries({ queryKey: ["customers"] });
            onDone();
          } catch (e) {
            toasts.show(e instanceof Error ? e.message : "Couldn't delete", true);
          } finally {
            setBusy(false);
          }
        }}>Delete data</button>
      </>
    }>
      <p>This removes {customer.name ?? "this customer"}'s name, phone number, chat messages, preferences, staff comments and feedback text.</p>
      <p>Orders, totals and consent records stay, without anything that identifies them. If they message again, they start as a new customer with no offers consent.</p>
      <p><strong>This can't be undone</strong> and is recorded in the audit log.</p>
    </Dialog>
  );
}

function ConsentRegister() {
  const q = useConsentRegister();
  const toasts = useToasts();
  async function exportCsv() {
    try {
      const csv = (await privacy.exportConsents()) as unknown as string;
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: "consent-register.csv" });
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't export", true);
    }
  }
  return (
    <section>
      <div className="row toolbar">
        <p className="grow muted">Only the customer can change their consent — on WhatsApp, or by replying STOP.</p>
        <button className="btn small" onClick={() => void exportCsv()}><Download size={15} /> Export CSV (full history)</button>
      </div>
      {q.isPending ? <Empty>Loading…</Empty> : !q.data?.length ? <Empty>No one has been asked yet.</Empty> : (
        <table className="table">
          <thead><tr><th>Customer</th><th>Status</th><th>How</th><th className="num">Wording</th><th>When</th></tr></thead>
          <tbody>{q.data.map((r) => (
            <tr key={r.customer_id}>
              <td>{r.name ?? "—"} <span className="mono small muted">{r.masked_phone}</span></td>
              <td><span className={`chip ${r.status === "opted_in" ? "ok" : "calm"}`}>{CONSENT[r.status] ?? r.status}</span></td>
              <td>{SOURCE[r.source] ?? r.source}</td><td className="num">{r.wording_version ? `v${r.wording_version}` : "—"}</td>
              <td>{dayMonth(r.created_at)}</td>
            </tr>))}</tbody>
        </table>
      )}
    </section>
  );
}
