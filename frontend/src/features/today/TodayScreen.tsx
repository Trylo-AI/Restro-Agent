// Today: the owner's home (docs/console.md#today). No sound here. KPIs refetch every 5 min; attention cards and
// "Service right now" follow Realtime through the shared hooks.
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CircleAlert, Megaphone } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, Navigate } from "react-router-dom";

import { useToasts } from "../../app/toasts";
import { Empty } from "../../components/ui";
import { useCafe, useLiveOrders } from "../../lib/data";
import { inr } from "../../lib/format";
import { cafes } from "../../lib/mutations";
import { useToday } from "../../lib/reports";
import { atLeast, useSession } from "../../lib/session";
import { Delta, Tile } from "../ops/common";

interface Card { key: string; critical: boolean; body: ReactNode }

export function TodayScreen() {
  const { role, cafeId } = useSession();
  const today = useToday();
  const cafe = useCafe();
  const orders = useLiveOrders();
  const toasts = useToasts();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  if (!atLeast(role, "owner")) return <Navigate to="/orders" replace />;

  const t = today.data;
  const c = cafe.data;
  const cards: Card[] = [];
  if (c?.status === "disconnected") {
    cards.push({ key: "disc", critical: true, body: <>WhatsApp is disconnected — customers can't order or get updates. The Restro team has been alerted.</> });
  }
  if (t && t.lowRatingsOpen > 0) {
    cards.push({ key: "low", critical: true, body: <>{t.lowRatingsOpen} low rating{t.lowRatingsOpen > 1 ? "s" : ""} not handled. <Link to="/calls">Open Calls</Link></> });
  }
  for (const name of t?.rejectedTemplates ?? []) {
    cards.push({ key: `tpl-${name}`, critical: false, body: <>WhatsApp rejected the template <span className="mono">{name}</span>. The Restro team will resubmit it.</> });
  }
  if (c?.settings?.paused) {
    cards.push({ key: "paused", critical: false, body: <>WhatsApp ordering is paused.{" "}
      <button className="btn small" disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          await cafes.pause(cafeId!, false);
          toasts.show("Ordering resumed");
        } catch (e) {
          toasts.show(e instanceof Error ? e.message : "Couldn't resume", true);
        } finally {
          setBusy(false);
          void qc.invalidateQueries({ queryKey: ["cafe", cafeId] });
        }
      }}>Resume</button></> });
  }
  const quality = c?.health?.quality;
  if (quality === "Medium" || quality === "Low") {
    cards.push({ key: "quality", critical: quality === "Low", body: <>Number quality is {quality}. Pause marketing until it recovers.</> });
  }
  cards.sort((a, b) => Number(b.critical) - Number(a.critical));

  const live = orders.data ?? [];
  const count = (s: string) => live.filter((o) => o.status === s).length;
  const overdue = live.filter((o) => o.flags.includes("overdue")).length;

  return (
    <div className="ops today">
      {today.isPending ? <Empty>Loading today…</Empty> : today.isError ? <Empty>Couldn't load today. <button className="btn small" onClick={() => void today.refetch()}>Try again</button></Empty> : t && (
        <div className="tiles">
          <Tile label="Customers today" value={t.customersToday}
            sub={<><Delta now={t.customersToday} before={t.customersSameDayLastWeek} worseWhenUp={false} /> (same day last week)</>} />
          <Tile label="Came back" value={t.cameBackPct === null ? "—" : `${t.cameBackPct} %`} sub="of today's customers had visited before" />
          <Tile label="New opt-ins" value={t.newOptIns} sub="said yes to offers today" />
          <Tile label="Offer redemptions" value={t.redemptionsThisWeek} sub="this week" to="/campaigns" />
        </div>
      )}

      <section className="panel wide">
        <div className="panel-head"><h2>Needs attention</h2></div>
        {cards.length ? (
          <ul className="attention">
            {cards.map((a) => (
              <li key={a.key} className={a.critical ? "critical" : "warning"}>
                {a.critical ? <CircleAlert size={16} aria-label="Critical" /> : <AlertTriangle size={16} aria-label="Warning" />}<span>{a.body}</span>
              </li>
            ))}
          </ul>
        ) : <p className="muted">Nothing needs you right now.</p>}
      </section>

      <div className="panels">
        <section className="panel">
          <div className="panel-head"><h2>Service right now</h2><Link to="/orders" className="small">Live orders →</Link></div>
          <div className="row wrap">
            <Link className="chip-btn" to="/orders">{count("NEW")} new</Link>
            <Link className="chip-btn" to="/orders">{count("ACCEPTED")} being made</Link>
            <Link className="chip-btn" to="/orders">{count("READY")} ready</Link>
            {overdue > 0 && <Link className="chip critical" to="/orders">{overdue} overdue</Link>}
          </div>
        </section>
        <section className="panel">
          <div className="panel-head"><h2>Next campaign</h2></div>
          {t?.nextCampaign
            ? <p><Megaphone size={15} /> <Link to={`/campaigns/${t.nextCampaign.id}`}>{t.nextCampaign.name}</Link> · {new Date(t.nextCampaign.scheduled_at).toLocaleString("en-IN")}</p>
            : <p className="muted">None scheduled. <Link to="/campaigns/new">New campaign</Link></p>}
        </section>
        {t && (
          <section className="panel">
            <div className="panel-head"><h2>Last week</h2><Link to="/analytics" className="small">Analytics →</Link></div>
            <p className="muted small">{t.lastWeek.start} to {t.lastWeek.end}</p>
            <dl className="facts">
              <dt>Revenue</dt><dd>{inr(t.lastWeek.revenue)}</dd>
              <dt>Orders</dt><dd>{t.lastWeek.orders}</dd>
              <dt>Returning</dt><dd>{t.lastWeek.returningPct === null ? "—" : `${t.lastWeek.returningPct} %`}</dd>
              <dt>Top dish</dt><dd>{t.lastWeek.topDish ?? "—"}</dd>
            </dl>
          </section>
        )}
      </div>
    </div>
  );
}
