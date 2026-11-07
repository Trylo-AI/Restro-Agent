// One campaign for the owner (docs/console.md#campaigns): Overview (funnel, tiles, redemptions by day), Recipients
// (server-side paging and filters), Redemptions and Opted out, plus the masked recipients CSV. Numbers come from
// v_campaign_insights / v_campaign_recipients / v_redemptions; money only as paise through inr().
import { useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useToasts } from "../../app/toasts";
import { Empty } from "../../components/ui";
import {
  byDay, exportRecipients, RECIPIENT_PAGE, useCampaignInsights, useCampaignRecipients, useRedemptions,
  type CampaignInsight, type Outcome, type RecipientFilters, type StatusFilter,
} from "../../lib/campaignInsights";
import type { CampaignRow } from "../../lib/data";
import { inr } from "../../lib/format";
import { campaigns } from "../../lib/mutations";
import { cafeClock, cafeDayMonth, channelLabel, DEFAULT_TZ, staffLabel, useCafeTimezone } from "../../lib/offers";
import { useSession } from "../../lib/session";
import { COLOR, StackedBars } from "../ops/charts";
import { Tile } from "../ops/common";
import { DEBOUNCE_MS } from "../redeem/RedeemScreen";
import { HELD_TIP, OPT_OUT_SOURCE, pct, RECIPIENT_STATUS, SEGMENT_LABEL, STATUS } from "./labels";

const TABS = [["overview", "Overview"], ["recipients", "Recipients"], ["redemptions", "Redemptions"], ["opted-out", "Opted out"]] as const;
type Tab = (typeof TABS)[number][0];

export const REDEEM_KEYS = [
  { key: "counter", label: "At the counter", color: COLOR.p50 }, // categorical slots 1 and 2
  { key: "table_bill", label: "On a table bill", color: COLOR.p95 },
];

/** The metered WhatsApp cost (numeric, not paise) as "0.86"; "—" while nothing is metered (the existing copy). */
export function costText(cost: number): string {
  return cost ? cost.toFixed(2) : "—";
}

/** "12 Oct, 12:41" in the café's zone. */
function when(iso: string | null, tz: string): string {
  return iso ? `${cafeDayMonth(iso, tz)}, ${cafeClock(iso, tz)}` : "—";
}

export function CampaignDetail({ campaign }: { campaign: CampaignRow }) {
  const c = campaign;
  const [sp, setSp] = useSearchParams();
  const tab = (TABS.find(([t]) => t === sp.get("tab"))?.[0] ?? "overview") as Tab;
  const ins = useCampaignInsights();
  const row = ins.data?.find((x) => x.campaign_id === c.id);
  const tz = useCafeTimezone().data ?? DEFAULT_TZ;
  const toasts = useToasts();
  const qc = useQueryClient();
  const { cafeId } = useSession();
  const navigate = useNavigate();
  const [exporting, setExporting] = useState(false);
  const status = row?.status ?? c.status; // the view refetches every 60 s, so a sending campaign's chip moves

  async function cancel() {
    try {
      await campaigns.cancel(c.id);
      toasts.show("Campaign cancelled · messages not yet sent won't go out");
      void qc.invalidateQueries({ queryKey: ["campaigns", cafeId] });
      void qc.invalidateQueries({ queryKey: ["campaign-insights", cafeId] });
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't cancel", true);
    }
  }

  async function download() {
    setExporting(true);
    try {
      const n = await exportRecipients(c.id);
      toasts.show(`Exported ${n} rows`);
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't export. Try again.", true);
    }
    setExporting(false);
  }

  return (
    <div className="campaigns">
      <div className="row toolbar detail-head">
        <button className="btn small ghost" onClick={() => navigate("/campaigns")}>← Campaigns</button>
        <h2 className="grow">{c.name} <span className={`chip ${STATUS[status].cls}`}>{STATUS[status].label}</span></h2>
        <button className="btn small" disabled={exporting} onClick={() => void download()}><Download size={15} /> Export CSV</button>
        {(status === "scheduled" || status === "sending") && <button className="btn danger small" onClick={() => void cancel()}>{status === "scheduled" ? "Cancel scheduled send" : "Stop sending"}</button>}
      </div>
      <p className="muted">{SEGMENT_LABEL[c.segment]} · {c.template_name} · {c.offer_text ?? "no offer text"}{c.discount_percent ? ` · ${c.discount_percent} % off the bill` : ""}
        {c.scheduled_at ? ` · ${new Date(c.scheduled_at).toLocaleString("en-IN", { timeZone: tz })}` : ""}
        {" · "}{c.valid_until ? `Codes valid till ${cafeDayMonth(c.valid_until, tz)}` : "No expiry (30-day window for results)"}</p>
      <nav className="tabs-row" aria-label="Campaign">
        {TABS.map(([t, label]) => (
          <button key={t} className={`chip-btn${tab === t ? " on" : ""}`} aria-pressed={tab === t}
            onClick={() => setSp(t === "overview" ? {} : { tab: t }, { replace: true })}>{label}</button>
        ))}
      </nav>
      {ins.isError ? <Empty>Couldn't load the results. <button className="btn small" onClick={() => void ins.refetch()}>Retry</button></Empty>
        : ins.isPending ? <Empty>Loading…</Empty>
          : !row ? <Empty>No results for this campaign yet.</Empty>
            : tab === "overview" ? <Overview c={row} />
              : tab === "recipients" ? <Recipients c={row} tz={tz} />
                : tab === "redemptions" ? <Redemptions campaignId={c.id} tz={tz} />
                  : <OptedOut c={row} tz={tz} />}
    </div>
  );
}

function Funnel({ c }: { c: CampaignInsight }) {
  const steps: [string, number][] = [
    ["Sent", c.sent], ["Delivered", c.delivered], ["Read", c.read], ["Replied", c.replied], ["Came back", c.came_back], ["Redeemed", c.redeemed],
  ];
  return (
    <ul className="funnel" aria-label="Funnel">
      {steps.map(([label, n]) => (
        <li key={label}>
          <span className="label">{label}</span>
          <span className="track"><span className="fill" style={{ width: c.sent ? `${Math.min(100, (n / c.sent) * 100)}%` : 0 }} /></span>
          <span className="val">{n} · {pct(n, c.sent)}</span>
        </li>
      ))}
    </ul>
  );
}

function Overview({ c }: { c: CampaignInsight }) {
  const r = useRedemptions({ campaignId: c.campaign_id });
  const days = byDay(r.data ?? []).map((d) => ({ t: d.t, counter: d.counter, table_bill: d.table_bill }));
  return (
    <>
      {c.sent ? <Funnel c={c} /> : <Empty>No messages sent yet.</Empty>}
      <ul className="side-counts" aria-label="Other counts">
        <li title={HELD_TIP}>Held by WhatsApp ⓘ <strong>{c.held}</strong></li>
        <li>Failed <strong>{c.failed}</strong></li>
        <li>Still to send <strong>{c.queued}</strong></li>
        <li>Opted out after <strong>{c.opted_out}</strong></li>
      </ul>
      <div className="tiles">
        <Tile label="Came-back revenue" value={inr(c.came_back_paise)} sub="WhatsApp orders in the offer window + counter bills with a code" />
        <Tile label="Discount given" value={inr(c.discount_paise)} />
        <Tile label="Redeemed" value={c.redeemed} sub={`${c.redeemed_counter} at the counter · ${c.redeemed_table_bill} on a table bill`} />
        <Tile label="WhatsApp cost" value={costText(c.whatsapp_cost)} sub={c.whatsapp_cost ? "metered" : "not tracked until GCP"} />
      </div>
      <section className="panel" aria-label="Redemptions by day">
        <div className="panel-head"><h2>Redemptions by day</h2></div>
        {r.isError ? <p className="error-text">Couldn't load redemptions. <button className="btn small" onClick={() => void r.refetch()}>Retry</button></p>
          : r.isPending ? <p className="muted">Loading…</p>
            : <StackedBars rows={days} keys={REDEEM_KEYS} spanS={Math.max(3, days.length) * 86_400} />}
      </section>
      <details className="glossary">
        <summary>What these mean</summary>
        <dl className="facts">
          <dt>Replied</dt><dd>Any message from the customer inside the offer window, an opt-out tap included.</dd>
          <dt>Came back</dt><dd>A WhatsApp order (not cancelled) inside the offer window, or a redemption of this code.</dd>
          <dt>Redeemed</dt><dd>The code was used, at the counter or on a table bill. The only hard proof.</dd>
          <dt>Offer window</dt><dd>From the send to the code's last day, or 30 days when the code has no expiry, up to now.</dd>
          <dt>Opted out after</dt><dd>Opted out of offers inside the offer window. The code still works.</dd>
        </dl>
      </details>
    </>
  );
}

const STATUS_CHIPS: [StatusFilter | null, string][] = [
  [null, "All"], ["not_sent", "Not sent"], ["sent", "Sent"], ["delivered", "Delivered"], ["read", "Read"], ["held", "Held"], ["failed", "Failed"],
];
const OUTCOME_CHIPS: [Outcome, string][] = [
  ["redeemed", "Redeemed"], ["not_redeemed", "Not redeemed"], ["came_back", "Came back"], ["replied", "Replied"], ["opted_out", "Opted out after"],
];

function Pager({ page, total, onPage }: { page: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / RECIPIENT_PAGE));
  if (pages <= 1) return null;
  return (
    <div className="row pager">
      <button className="btn small" disabled={page === 0} onClick={() => onPage(page - 1)}>Previous</button>
      <span className="muted small">Page {page + 1} of {pages}</span>
      <button className="btn small" disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  );
}

function Recipients({ c, tz }: { c: CampaignInsight; tz: string }) {
  const [filters, setFilters] = useState<RecipientFilters>({ status: null, outcomes: [], name: "" });
  const [name, setName] = useState(""); // what is typed; committed to filters.name after the same 300 ms as Redeem
  const [page, setPage] = useState(0);
  const q = useCampaignRecipients(c.campaign_id, filters, page);
  const set = (f: Partial<RecipientFilters>) => { setFilters((x) => ({ ...x, ...f })); setPage(0); };
  useEffect(() => {
    if (name === filters.name) return;
    const id = window.setTimeout(() => { setFilters((x) => ({ ...x, name })); setPage(0); }, DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [name, filters.name]);
  const loading = q.isPlaceholderData; // the last page stays on screen while the next filter or page loads
  const toggle = (o: Outcome) => set({
    outcomes: filters.outcomes.includes(o) ? filters.outcomes.filter((x) => x !== o)
      // Redeemed and Not redeemed exclude each other
      : [...filters.outcomes.filter((x) => !(o === "redeemed" && x === "not_redeemed") && !(o === "not_redeemed" && x === "redeemed")), o],
  });
  return (
    <>
      <div className="row toolbar recipients-filters">
        <input type="search" className="grow" aria-label="Search recipients by name" placeholder="Search by name" maxLength={60}
          value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="row filters" role="group" aria-label="Status">
        {STATUS_CHIPS.map(([s, label]) => (
          <button key={label} className={`chip-btn${filters.status === s ? " on" : ""}`} aria-pressed={filters.status === s}
            onClick={() => set({ status: s })}>{label}</button>
        ))}
      </div>
      <div className="row filters" role="group" aria-label="Outcome">
        {OUTCOME_CHIPS.map(([o, label]) => (
          <button key={o} className={`chip-btn${filters.outcomes.includes(o) ? " on" : ""}`} aria-pressed={filters.outcomes.includes(o)}
            onClick={() => toggle(o)}>{label}</button>
        ))}
      </div>
      <p className="muted small count-line" aria-live="polite">{c.recipients} recipients{q.data ? ` · ${q.data.total} match` : ""}{loading ? " · updating…" : ""}</p>
      {q.isError ? <Empty>Couldn't load recipients. <button className="btn small" onClick={() => void q.refetch()}>Retry</button></Empty>
        : !q.data ? <Empty>Loading…</Empty>
          : !q.data.rows.length ? <Empty>No recipients match these filters.</Empty> : (
            <table className="table card-table" aria-busy={loading}>
              <thead><tr><th>Customer</th><th>Code</th><th>Status</th><th>Sent / read</th><th>Replied</th><th className="num">Orders in window</th><th>Redeemed</th><th>Opted out after</th></tr></thead>
              <tbody>{q.data.rows.map((r) => (
                <tr key={r.recipient_id}>
                  <td data-label="Customer">{r.deleted ? "Deleted customer" : r.name ?? "No name"}{r.masked_phone && <div className="muted mono small">{r.masked_phone}</div>}</td>
                  <td data-label="Code" className="mono">{r.code}</td>
                  <td data-label="Status"><span className={`chip ${RECIPIENT_STATUS[r.status].cls}`}>{RECIPIENT_STATUS[r.status].label}</span></td>
                  <td data-label="Sent / read">{when(r.sent_at, tz)}{r.read_at && <div className="muted small">read {when(r.read_at, tz)}</div>}</td>
                  <td data-label="Replied">{r.replied_at ? when(r.replied_at, tz) : "—"}</td>
                  <td data-label="Orders in window" className="num">{r.orders ? `${r.orders} · ${inr(r.spent_paise)}` : "—"}</td>
                  <td data-label="Redeemed">{r.redeemed_at
                    ? <>{when(r.redeemed_at, tz)} · {channelLabel({ channel: r.redemption_channel ?? "table_bill" })}
                      {` · by ${staffLabel(r)}`} · −{inr(r.discount_paise ?? 0)}</>
                    : "—"}</td>
                  <td data-label="Opted out after">{r.opted_out_at ? `${cafeDayMonth(r.opted_out_at, tz)} · ${OPT_OUT_SOURCE[r.opted_out_source ?? ""] ?? r.opted_out_source ?? ""}` : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
      {q.data && <Pager page={page} total={q.data.total} onPage={setPage} />}
    </>
  );
}

function Redemptions({ campaignId, tz }: { campaignId: string; tz: string }) {
  const q = useRedemptions({ campaignId });
  if (q.isError) return <Empty>Couldn't load redemptions. <button className="btn small" onClick={() => void q.refetch()}>Retry</button></Empty>;
  if (!q.data) return <Empty>Loading…</Empty>;
  if (!q.data.length) return <Empty>No codes redeemed yet.</Empty>;
  return (
    <table className="table card-table">
      <thead><tr><th>Time</th><th>Customer</th><th>Code</th><th>Where</th><th className="num">Bill</th><th className="num">Discount</th><th>Staff</th></tr></thead>
      <tbody>{q.data.map((r) => (
        <tr key={r.redemption_id}>
          <td data-label="Time">{when(r.redeemed_at, tz)}</td>
          <td data-label="Customer">{r.first_name ?? "Customer"}{r.masked_phone && <div className="muted mono small">{r.masked_phone}</div>}</td>
          <td data-label="Code" className="mono">{r.code}</td>
          <td data-label="Where">{channelLabel(r, true)}</td>
          <td data-label="Bill" className="num">{r.bill_paise === null ? "—" : inr(r.bill_paise)}</td>
          <td data-label="Discount" className="num">−{inr(r.discount_paise)}</td>
          <td data-label="Staff">{staffLabel(r)}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

function OptedOut({ c, tz }: { c: CampaignInsight; tz: string }) {
  const [page, setPage] = useState(0);
  const q = useCampaignRecipients(c.campaign_id, { status: null, outcomes: ["opted_out"], name: "" }, page);
  return (
    <>
      <p className="muted">Opted out within the offer window after this campaign was sent.</p>
      {q.isError ? <Empty>Couldn't load opt-outs. <button className="btn small" onClick={() => void q.refetch()}>Retry</button></Empty>
        : !q.data ? <Empty>Loading…</Empty>
          : !q.data.rows.length ? <Empty>Nobody opted out after this campaign.</Empty> : (
            <table className="table card-table">
              <thead><tr><th>Customer</th><th>When</th><th>How</th></tr></thead>
              <tbody>{q.data.rows.map((r) => (
                <tr key={r.recipient_id}>
                  <td data-label="Customer">{r.deleted ? "Deleted customer" : r.name ?? "No name"}{r.masked_phone && <div className="muted mono small">{r.masked_phone}</div>}</td>
                  <td data-label="When">{when(r.opted_out_at, tz)}</td>
                  <td data-label="How">{OPT_OUT_SOURCE[r.opted_out_source ?? ""] ?? r.opted_out_source ?? "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
      {q.data && <Pager page={page} total={q.data.total} onPage={setPage} />}
    </>
  );
}
