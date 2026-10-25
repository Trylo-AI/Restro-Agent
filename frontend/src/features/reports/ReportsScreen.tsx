// Service report (manager+, fixed last 7 days) and Analytics (owner, 7 / 30 / 90 days): docs/console.md#analytics-and-service-report.
// Read-only from the analytics views. Revenue = WhatsApp orders only, cancelled excluded.
import { useState } from "react";
import { Navigate } from "react-router-dom";

import { Empty } from "../../components/ui";
import { inr } from "../../lib/format";
import { useAnalytics, useServiceReport, type FeedbackRow, type PrepSummary } from "../../lib/reports";
import { atLeast, useSession } from "../../lib/session";
import { COLOR, StackedBars } from "../ops/charts";
import { Delta, Panel, Tile } from "../ops/common";

const hourLabel = (h: number | null) => (h === null ? "—" : `${String(h).padStart(2, "0")}:00–${String((h + 1) % 24).padStart(2, "0")}:00`);
const stars = (n: number) => "★".repeat(n) + "☆".repeat(5 - n);

function LowRatings({ rows }: { rows: FeedbackRow[] }) {
  if (!rows.length) return <p className="muted">No 1–3★ ratings in this period.</p>;
  return (
    <ul className="plist">
      {rows.map((f) => (
        <li key={f.order_id}><span aria-label={`${f.rating} of 5`}>{stars(f.rating)}</span> <span className="muted">#{f.number} · {f.day}</span>
          {f.text ? <div>“{f.text}”</div> : <div className="muted small">No comment</div>}</li>
      ))}
    </ul>
  );
}

function ServiceBlock({ s, orders }: { s: PrepSummary; orders?: number }) {
  return (
    <>
      <div className="tiles">
        <Tile label="Median prep (placed → ready)" value={s.median === null ? "—" : `${s.median} min`} sub={orders !== undefined ? `${orders} orders` : undefined} />
        <Tile label="Orders overdue" value={s.pctOverdue === null ? "—" : `${s.pctOverdue} %`} tone={(s.pctOverdue ?? 0) > 10 ? "warning" : undefined} />
        <Tile label="Peak overdue hour" value={hourLabel(s.peakHour)} />
        <Tile label="Slowest dish" value={s.slowest[0]?.dish ?? "—"} sub={s.slowest[0] ? `${s.slowest[0].median} min median` : undefined} />
      </div>
      {s.slowest.length > 0 && (
        <table className="table"><thead><tr><th>Slowest dishes</th><th className="num">Median prep</th><th className="num">Orders</th></tr></thead>
          <tbody>{s.slowest.map((d) => <tr key={d.dish}><td>{d.dish}</td><td className="num">{d.median} min</td><td className="num">{d.orders}</td></tr>)}</tbody></table>
      )}
    </>
  );
}

export function ServiceReportScreen() {
  const { role } = useSession();
  const q = useServiceReport();
  if (!atLeast(role, "manager")) return <Navigate to="/orders" replace />;
  if (q.isPending) return <Empty>Loading the service report…</Empty>;
  if (q.isError) return <Empty>Couldn't load the report. <button className="btn small" onClick={() => void q.refetch()}>Try again</button></Empty>;
  const r = q.data.report;
  const ratings = q.data.feedback;
  const avg = ratings.length ? (ratings.reduce((s, f) => s + f.rating, 0) / ratings.length).toFixed(1) : null;
  return (
    <div className="ops report">
      <p className="muted">Last 7 days · WhatsApp orders only.</p>
      {r ? <ServiceBlock orders={r.orders} s={{
        median: r.median_prep_minutes, pctOverdue: r.pct_overdue, peakHour: r.peak_overdue_hour,
        slowest: (r.slowest_dishes ?? []).map((d) => ({ dish: d.dish, median: d.median_minutes, orders: d.orders })),
      }} /> : <Empty>No orders reached Ready in the last 7 days.</Empty>}
      <Panel title={`Ratings · ${avg ? `${avg}★ average from ${ratings.length}` : "none yet"}`} wide>
        <LowRatings rows={ratings.filter((f) => f.rating <= 3)} />
      </Panel>
    </div>
  );
}

export function AnalyticsScreen() {
  const { role } = useSession();
  const [days, setDays] = useState<7 | 30 | 90>(30);
  const q = useAnalytics(days);
  if (!atLeast(role, "owner")) return <Navigate to="/orders" replace />;
  const d = q.data;
  // Weekly bars at 90 days, daily otherwise.
  const bars = !d ? [] : days === 90
    ? Object.values(d.series.reduce<Record<string, { t: number; revenue: number }>>((acc, x) => {
        const date = new Date(`${x.day}T00:00:00`);
        const monday = new Date(date.getTime() - ((date.getDay() + 6) % 7) * 86_400_000);
        const key = monday.toISOString().slice(0, 10);
        acc[key] = { t: monday.getTime(), revenue: (acc[key]?.revenue ?? 0) + Number(x.revenue_paise) };
        return acc;
      }, {}))
    : d.series.map((x) => ({ t: new Date(`${x.day}T00:00:00`).getTime(), revenue: Number(x.revenue_paise) }));

  return (
    <div className="ops report">
      <div className="row toolbar">
        <div className="segmented" role="radiogroup" aria-label="Period">
          {([7, 30, 90] as const).map((n) => <button key={n} role="radio" aria-checked={days === n} className={days === n ? "on" : ""} onClick={() => setDays(n)}>{n} days</button>)}
        </div>
        <span className="muted small grow">WhatsApp orders only; walk-in cash and UPI aren't included.</span>
      </div>
      {q.isPending ? <Empty>Loading analytics…</Empty> : q.isError ? <Empty>Couldn't load analytics. <button className="btn small" onClick={() => void q.refetch()}>Try again</button></Empty> : d && (
        <>
          <div className="tiles">
            <Tile label="Revenue" value={inr(d.current.revenue)} sub={<Delta now={d.current.revenue} before={d.previous.revenue} worseWhenUp={false} />} />
            <Tile label="Orders" value={d.current.orders} sub={<Delta now={d.current.orders} before={d.previous.orders} worseWhenUp={false} />} />
            <Tile label="Average order" value={d.current.avgOrder === null ? "—" : inr(d.current.avgOrder)} sub={<Delta now={d.current.avgOrder} before={d.previous.avgOrder} worseWhenUp={false} />} />
            <Tile label="Average rating" value={d.current.avgRating === null ? "—" : `${d.current.avgRating}★`} sub={<Delta now={d.current.avgRating} before={d.previous.avgRating} worseWhenUp={false} />} />
            <Tile label="Returning customers" value={d.current.returningPct === null ? "—" : `${d.current.returningPct} %`} sub={<>{d.current.customers} customers · <Delta now={d.current.returningPct} before={d.previous.returningPct} worseWhenUp={false} /></>} />
          </div>
          <div className="panels">
            <Panel title={days === 90 ? "Revenue by week" : "Revenue by day"} wide>
              <StackedBars rows={bars} keys={[{ key: "revenue", label: "Revenue", color: COLOR.bar }]} spanS={days * 86_400}
                format={(v) => inr(Math.round(v))} legend={false} />
            </Panel>
            <Panel title="Top 5 dishes">
              {d.top5.length ? (
                <table className="table"><thead><tr><th>Dish</th><th className="num">Sold</th><th className="num">Revenue</th></tr></thead>
                  <tbody>{d.top5.map((t) => <tr key={t.name}><td>{t.name}</td><td className="num">{t.qty}</td><td className="num">{inr(t.revenue)}</td></tr>)}</tbody></table>
              ) : <p className="muted">No orders in this period.</p>}
            </Panel>
            <Panel title="Service in this period">
              <ServiceBlock s={d.service} />
            </Panel>
            <Panel title="1–3★ ratings" wide><LowRatings rows={d.lowRatings} /></Panel>
          </div>
        </>
      )}
    </div>
  );
}
