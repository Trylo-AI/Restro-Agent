// Every campaign at once for the owner (docs/console.md#campaigns, Insights): KPI tiles, the best campaign, a sortable
// comparison, redemptions by day and by staff, and the latest redemptions. Campaigns are filtered by scheduled_at,
// redemptions by their café-local day. A campaign scheduled or cancelled before sending anything isn't counted. A visit
// can count for more than one campaign, so revenue is summed per campaign.
import { ArrowUpDown, Trophy } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Empty } from "../../components/ui";
import {
  bestCampaign, byDay, byStaff, REDEMPTION_CAP, totals, useCampaignInsights, useRedemptions, type CampaignInsight,
} from "../../lib/campaignInsights";
import { inr } from "../../lib/format";
import { cafeClock, cafeDay, cafeDayMonth, channelLabel, DEFAULT_TZ, staffLabel, useCafeTimezone } from "../../lib/offers";
import { StackedBars } from "../ops/charts";
import { Panel, Tile } from "../ops/common";
import { costText, REDEEM_KEYS } from "./CampaignDetail";
import { pct } from "./labels";

const PERIODS = [["30", "30 d"], ["90", "90 d"], ["all", "All"]] as const;
type Period = (typeof PERIODS)[number][0];

type SortKey = "name" | "sent" | "delivered" | "read" | "came_back" | "redeemed" | "came_back_paise" | "discount_paise" | "whatsapp_cost";
const COLUMNS: [SortKey, string][] = [
  ["name", "Name"], ["sent", "Sent"], ["delivered", "Delivered"], ["read", "Read"], ["came_back", "Came back"], ["redeemed", "Redeemed"],
  ["came_back_paise", "Revenue"], ["discount_paise", "Discount"], ["whatsapp_cost", "Cost"],
];

/** The value a column sorts by: rates for delivered / read / came back, counts and money otherwise. */
function sortValue(c: CampaignInsight, k: SortKey): number | string {
  if (k === "name") return c.name.toLowerCase();
  if (k === "delivered" || k === "read" || k === "came_back") return c.sent ? c[k] / c.sent : -1;
  return Number(c[k]);
}

export function CampaignInsights() {
  const [period, setPeriod] = useState<Period>("90");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "sent", desc: true });
  const tz = useCafeTimezone().data ?? DEFAULT_TZ;
  const navigate = useNavigate();
  const since = period === "all" ? null : new Date(Date.now() - Number(period) * 86_400_000);
  const sinceDay = since ? cafeDay(tz, since) : null;
  const ins = useCampaignInsights();
  const red = useRedemptions({ sinceDay });

  if (ins.isError) return <Empty>Couldn't load insights. <button className="btn small" onClick={() => void ins.refetch()}>Retry</button></Empty>;
  if (ins.isPending) return <Empty>Loading insights…</Empty>;

  const list = ins.data.filter((c) => c.scheduled_at && c.status !== "draft"
    && !(c.sent === 0 && (c.status === "scheduled" || c.status === "cancelled")) // nothing went out
    && (!since || Date.parse(c.scheduled_at) >= since.getTime()));
  const t = totals(list);
  const best = bestCampaign(list);
  const redemptions = red.data ?? [];
  // until v_redemptions answers, the three panels say so instead of a false "No redemptions"
  const redState = red.isError
    ? <p className="error-text">Couldn't load redemptions. <button className="btn small" onClick={() => void red.refetch()}>Retry</button></p>
    : red.isPending ? <p className="muted">Loading…</p> : null;
  const days = byDay(redemptions).map((d) => ({ t: d.t, counter: d.counter, table_bill: d.table_bill }));
  const staff = byStaff(redemptions);
  const sorted = [...list].sort((a, b) => {
    const x = sortValue(a, sort.key), y = sortValue(b, sort.key);
    const d = x < y ? -1 : x > y ? 1 : 0;
    return sort.desc ? -d : d;
  });
  const sortBy = (key: SortKey) => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "name" }));

  return (
    <div className="insights">
      <div className="row filters" role="group" aria-label="Period">
        {PERIODS.map(([p, label]) => (
          <button key={p} className={`chip-btn${period === p ? " on" : ""}`} aria-pressed={period === p} onClick={() => setPeriod(p)}>{label}</button>
        ))}
      </div>
      {!list.some((c) => c.sent > 0) ? <Empty>No campaigns sent in this period.</Empty> : (
        <>
          <div className="tiles">
            <Tile label="Offers delivered" value={t.delivered} />
            <Tile label="Redeemed" value={t.redeemed} sub={`${pct(t.redeemed, t.delivered)} of delivered`} />
            <Tile label="Came back" value={t.came_back} sub={`${pct(t.came_back, t.sent)} of sent`} />
            <Tile label="Came-back revenue" value={inr(t.came_back_paise)} sub="Summed per campaign; a visit can count for more than one campaign" />
            <Tile label="Discount given" value={inr(t.discount_paise)} />
            <Tile label="Opted out after" value={t.opted_out} />
          </div>
          <section className="best-campaign" aria-label="Best campaign">
            <Trophy size={18} aria-hidden />
            {best ? (
              <span><strong>Best campaign: <Link to={`/campaigns/${best.campaign_id}`}>{best.name}</Link></strong>
                <span className="muted"> · {pct(best.came_back, best.sent)} came back · {best.redeemed} redeemed</span></span>
            ) : <span className="muted">Not enough results yet: a campaign needs 10 delivered messages</span>}
          </section>
          <Panel title="Compare campaigns" wide>
            {/* phones show cards without the header row, so the sort moves to a select */}
            <div className="row sort-phone">
              <label>Sort by <select value={sort.key} onChange={(e) => setSort({ key: e.target.value as SortKey, desc: e.target.value !== "name" })}>
                {COLUMNS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select></label>
              <button className="btn small" onClick={() => setSort((s) => ({ ...s, desc: !s.desc }))}>
                <ArrowUpDown size={13} aria-hidden /> {sort.desc ? "Descending" : "Ascending"}</button>
            </div>
            <table className="table card-table" aria-label="Compare campaigns">
              <thead><tr>{COLUMNS.map(([k, label]) => (
                <th key={k} className={k === "name" ? "" : "num"} aria-sort={sort.key === k ? (sort.desc ? "descending" : "ascending") : "none"}>
                  <button className="link sort" onClick={() => sortBy(k)}>{label} <ArrowUpDown size={11} aria-hidden /></button>
                </th>
              ))}</tr></thead>
              <tbody>{sorted.map((c) => (
                <tr key={c.campaign_id} className="clickable" onClick={() => navigate(`/campaigns/${c.campaign_id}`)}>
                  <td data-label="Name"><button className="link">{c.name}</button></td>
                  <td data-label="Sent" className="num">{c.sent}</td>
                  <td data-label="Delivered" className="num">{pct(c.delivered, c.sent)}</td>
                  <td data-label="Read" className="num">{pct(c.read, c.sent)}</td>
                  <td data-label="Came back" className="num">{pct(c.came_back, c.sent)}</td>
                  <td data-label="Redeemed" className="num">{c.redeemed}</td>
                  <td data-label="Revenue" className="num">{inr(c.came_back_paise)}</td>
                  <td data-label="Discount" className="num">{inr(c.discount_paise)}</td>
                  <td data-label="Cost" className="num">{costText(c.whatsapp_cost)}</td>
                </tr>
              ))}</tbody>
            </table>
          </Panel>
        </>
      )}
      {redemptions.length >= REDEMPTION_CAP && (
        <p className="hint">Showing the latest {REDEMPTION_CAP.toLocaleString("en-IN")} redemptions; older ones in this period aren't in the
          panels below.</p>
      )}
      <Panel title="Redemptions by day" wide>
        {redState ?? <StackedBars rows={days} keys={REDEEM_KEYS} spanS={Math.max(3, days.length) * 86_400} />}
      </Panel>
      <Panel title="Redemptions by staff" wide>
        {redState ?? (!staff.length ? <p className="muted">No redemptions in this period.</p> : (
          <table className="table card-table" aria-label="Redemptions by staff">
            <thead><tr><th>Name</th><th className="num">Total</th><th className="num">At counter</th><th className="num">On bills</th><th className="num">Discount</th><th>Last</th></tr></thead>
            <tbody>{staff.map((s) => (
              <tr key={s.key}>
                <td data-label="Name">{s.name}</td>
                <td data-label="Total" className="num">{s.total}</td>
                <td data-label="At counter" className="num">{s.counter}</td>
                <td data-label="On bills" className="num">{s.tableBill}</td>
                <td data-label="Discount" className="num">{inr(s.discount_paise)}</td>
                <td data-label="Last">{cafeDayMonth(s.last, tz)}, {cafeClock(s.last, tz)}</td>
              </tr>
            ))}</tbody>
          </table>
        ))}
      </Panel>
      <Panel title="Recent redemptions" wide>
        {redState ?? (!redemptions.length ? <p className="muted">No redemptions in this period.</p> : (
          <ul className="plist">{redemptions.slice(0, 25).map((r) => (
            <li key={r.redemption_id} className="redeemed-row">
              <span className="mono">{cafeDayMonth(r.redeemed_at, tz)}, {cafeClock(r.redeemed_at, tz)}</span>
              <span>{r.first_name ?? "Customer"}</span>
              <span className="mono">{r.code}</span>
              <span>{channelLabel(r, true)}</span>
              <span>−{inr(r.discount_paise)}</span>
              <span className="muted">by {staffLabel(r)}</span>
              <span className="muted small">{r.campaign_name}</span>
            </li>
          ))}</ul>
        ))}
      </Panel>
    </div>
  );
}
