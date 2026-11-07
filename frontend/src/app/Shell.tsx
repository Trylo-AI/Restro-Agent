// Shell: sidebar (≥ 768 px) or tab bar, header with the active café, banners, toasts, alerts gate.
import { useQueryClient } from "@tanstack/react-query";
import { Activity, BarChart3, BellRing, House, Megaphone, Users, ClipboardList, Gauge, MessageCircle, PhoneCall, ScrollText, Settings, TicketPercent, UtensilsCrossed } from "lucide-react";
import { Suspense, useEffect, useMemo, useRef, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { Badge } from "../components/ui";
import { useConnection } from "../lib/connection";
import { useCafe, useLiveOrders } from "../lib/data";
import { atLeast, useSession } from "../lib/session";
import { AlertsGate, useAlerts } from "./alerts";
import { Bell } from "./Bell";
import { ErrorBoundary } from "./ErrorBoundary";
import { useToasts } from "./toasts";

const NAV = [
  { to: "/orders", label: "Live orders", short: "Orders", icon: ClipboardList, area: "orders" as const },
  { to: "/calls", label: "Calls and bills", short: "Calls", icon: PhoneCall, area: "calls" as const },
  { to: "/chats", label: "Chats", short: "Chats", icon: MessageCircle, area: "chats" as const },
  { to: "/menu", label: "Menu", short: "Menu", icon: UtensilsCrossed, area: null },
  { to: "/redeem", label: "Redeem offer", short: "Redeem", icon: TicketPercent, area: null },
];

const TITLES: Record<string, string> = { "/orders": "Live orders", "/calls": "Calls and bills", "/chats": "Chats", "/menu": "Menu", "/setup": "Setup", "/service-report": "Service report", "/analytics": "Analytics", "/customers": "Customers", "/campaigns": "Campaigns", "/today": "Today", "/platform": "Performance", "/redeem": "Redeem offer", "/audit": "Audit log" };

function Banners({ offline, retry }: { offline: boolean; retry: () => void }) {
  const cafe = useCafe();
  const { role } = useSession();
  const { soundOn, enableSound } = useAlerts();
  const out: ReactNode[] = [];
  if (cafe.data?.status === "disconnected") {
    out.push(<div key="d" className="banner critical">WhatsApp is disconnected — customers can't order or get updates. The Restro team has been alerted.</div>);
  }
  if (offline) {
    out.push(<div key="o" className="banner critical">Offline — new orders may be missing until you're back. <button className="btn small" onClick={retry}>Retry</button></div>);
  }
  if (cafe.data?.settings?.paused) {
    out.push(<div key="p" className="banner warning">WhatsApp ordering is paused.{atLeast(role, "manager") ? " Resume it from Live orders." : ""}</div>);
  }
  if (!soundOn) {
    out.push(<div key="a" className="banner warning"><BellRing size={16} /> Alerts are off on this device. <button className="btn small" onClick={() => void enableSound()}>Enable alerts</button></div>);
  }
  return <>{out}</>;
}

export function Shell() {
  const { cafeName, memberships, cafeId, switchCafe, displayName, role, signOut } = useSession();
  const { badges } = useAlerts();
  const { offline, retry, cameBackAt } = useConnection();
  const toasts = useToasts();
  const qc = useQueryClient();
  const orders = useLiveOrders();
  const location = useLocation();
  const countAtDrop = useRef<number | null>(null);

  useEffect(() => {
    if (offline) countAtDrop.current = orders.data?.length ?? 0;
  }, [offline]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!cameBackAt) return;
    void qc.invalidateQueries();
    const before = countAtDrop.current ?? 0;
    window.setTimeout(() => {
      const after = (qc.getQueryData<unknown[]>(["orders", cafeId]) ?? []).length;
      toasts.show(`Back online · ${Math.max(0, after - before)} orders arrived while offline`);
    }, 1500);
  }, [cameBackAt]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => toasts.clearUndo(), [location.pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  const end = () => {
    toasts.clearUndo();
    void signOut();
  };

  const title = TITLES[`/${location.pathname.split("/")[1]}`] ?? "";
  const outlet = useMemo(() => ({ offline }), [offline]); // a new object would re-render every useOffline() screen

  return (
    <AlertsGate>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">Restro</div>
          <div className="cafe-block">
            <div className="muted">Café</div>
            <div className="name">{cafeName}</div>
            {memberships.length > 1 && (
              <select value={cafeId ?? ""} onChange={(e) => { switchCafe(e.target.value); toasts.clearUndo(); toasts.show(`Switched to ${memberships.find((m) => m.cafe_id === e.target.value)?.cafes?.name}`); }} aria-label="Switch café">
                {memberships.map((m) => <option key={m.cafe_id} value={m.cafe_id}>{m.cafes?.name}</option>)}
              </select>
            )}
          </div>
          <nav className="nav">
            <div className="nav-group">Service</div>
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "active" : "")}>
                <n.icon size={18} /> {n.label}
                {n.area && <Badge {...badges[n.area]} />}
              </NavLink>
            ))}
            {atLeast(role, "manager") && (
              <>
                <div className="nav-group">{atLeast(role, "owner") ? "Owner" : "Manager"}</div>
                <NavLink to="/service-report" className={({ isActive }) => (isActive ? "active" : "")}><Gauge size={18} /> Service report</NavLink>
              </>
            )}
            {atLeast(role, "owner") && (
              <>
                <NavLink to="/today" className={({ isActive }) => (isActive ? "active" : "")}><House size={18} /> Today</NavLink>
                <NavLink to="/customers" className={({ isActive }) => (isActive ? "active" : "")}><Users size={18} /> Customers</NavLink>
                <NavLink to="/campaigns" className={({ isActive }) => (isActive ? "active" : "")}><Megaphone size={18} /> Campaigns</NavLink>
                <NavLink to="/analytics" className={({ isActive }) => (isActive ? "active" : "")}><BarChart3 size={18} /> Analytics</NavLink>
                <NavLink to="/setup/tables" className={({ isActive }) => (isActive ? "active" : "")}><Settings size={18} /> Setup</NavLink>
                <NavLink to="/audit" className={({ isActive }) => (isActive ? "active" : "")}><ScrollText size={18} /> Audit log</NavLink>
              </>
            )}
            {atLeast(role, "owner") && (
              <>
                {/* Platform tooling; the ops token is the real gate until platform_admin login exists (OPS_PLAN Q-E). */}
                <div className="nav-group">Platform</div>
                <NavLink to="/platform/ops/overview" className={({ isActive }) => (isActive ? "active" : "")}><Activity size={18} /> Performance</NavLink>
              </>
            )}
          </nav>
          <div className="user-block">
            <div><strong>{displayName ?? "You"}</strong> · {role === "shift_manager" ? "Shift manager" : role === "manager" ? "Manager" : "Owner"}</div>
            <button className="btn small" onClick={end}>Sign out</button>
          </div>
        </aside>
        <div className="main">
          <header className="header">
            <h1>{title}</h1>
            <span className="cafe">{cafeName}</span>
            <span className="spacer" />
            <Bell />
            <button className="btn small ghost hide-desktop" onClick={end}>Sign out</button>
          </header>
          <Banners offline={offline} retry={retry} />
          <div className="content">
            <ErrorBoundary scope="screen" key={location.pathname}>
              <Suspense fallback={<div className="empty">Loading…</div>}>
                <Outlet context={outlet} />
              </Suspense>
            </ErrorBoundary>
          </div>
        </div>
        {/* One column per entry (app.css says 4; Redeem made it 5): ~75 px each at 375 px, no horizontal scroll. */}
        <nav className="tabbar" style={{ gridTemplateColumns: `repeat(${NAV.length}, 1fr)` }}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "active" : "")}>
              <n.icon size={20} /> {n.short}
              {n.area && <Badge {...badges[n.area]} />}
            </NavLink>
          ))}
        </nav>
      </div>
    </AlertsGate>
  );
}
