// Shell-level urgency: badges, ringing, notifications and the alerts gate (docs/console.md#urgency-and-alerts).
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { useChats, useLiveOrders, useOpenRequests } from "../lib/data";
import { usePhone } from "../lib/media";
import { atLeast, useSession } from "../lib/session";
import { notify, notifyPermission, play, soundEnabled, unlock } from "../lib/sound";
import { Ringer, badge, chatUrgency, orderUrgency, requestUrgency, type Tracked } from "../lib/urgency";

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

interface AlertState {
  badges: Record<"orders" | "calls" | "chats", { count: number; critical: boolean }>;
  soundOn: boolean;
  enableSound(): Promise<void>;
}

const Ctx = createContext<AlertState | null>(null);
export const useAlerts = (): AlertState => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAlerts outside AlertProvider");
  return v;
};

export function AlertProvider({ children }: { children: ReactNode }) {
  const { role, cafeId } = useSession();
  const now = useNow();
  const orders = useLiveOrders();
  const requests = useOpenRequests();
  const chats = useChats();
  const [soundOn, setSoundOn] = useState(soundEnabled());
  const ringer = useRef(new Ringer());

  useEffect(() => ringer.current.reset(), [cafeId]); // café switch: record silently

  const tracked = useMemo(() => {
    const out: (Tracked & { title: string; area: "orders" | "calls" | "chats" })[] = [];
    for (const o of orders.data ?? []) {
      out.push({ key: `o:${o.id}`, urgency: orderUrgency(o, now), title: `${orderUrgency(o, now).chip} · ${o.table ?? "?"} #${o.number}`, area: "orders" });
    }
    for (const r of requests.data ?? []) {
      if (r.kind === "low_rating" && !atLeast(role, "manager")) continue;
      const u = requestUrgency(r, now, r.ackName);
      out.push({ key: `r:${r.id}:${r.status}`, urgency: u, title: `${u.chip} · ${r.table ?? "?"}`, area: "calls" });
    }
    for (const c of chats.data ?? []) {
      const u = chatUrgency(c.mode, c.lastInAt, c.lastOutAt, now);
      out.push({ key: `c:${c.id}`, urgency: u, title: `${u.chip} · ${c.customerName ?? "Chat"}`, area: "chats" });
    }
    return out;
  }, [orders.data, requests.data, chats.data, now, role]);

  const loaded = orders.isSuccess && requests.isSuccess && chats.isSuccess;
  useEffect(() => {
    if (!loaded) return;
    const sound = ringer.current.tick(tracked, now);
    if (sound) {
      play(sound);
      const top = tracked.filter((t) => t.urgency.sound === sound)[0];
      if (top) notify(top.title, "Restro Console", top.urgency.severity === "critical");
    }
  }, [tracked, now, loaded]);

  const badges = {
    orders: badge(tracked.filter((t) => t.area === "orders").map((t) => t.urgency)),
    calls: badge(tracked.filter((t) => t.area === "calls").map((t) => t.urgency)),
    chats: badge(tracked.filter((t) => t.area === "chats").map((t) => t.urgency)),
  };
  // The clock ticks every second, but the shell only needs a new value when a badge or the sound state changes.
  const badgeKey = JSON.stringify(badges);
  const value = useMemo<AlertState>(() => ({
    badges,
    soundOn,
    async enableSound() {
      const ok = await unlock();
      await notifyPermission();
      setSoundOn(ok);
    },
  }), [badgeKey, soundOn]); // eslint-disable-line react-hooks/exhaustive-deps

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Phones, shift_manager and manager, sound off → full screen until alerts are turned on. */
export function AlertsGate({ children }: { children: ReactNode }) {
  const { soundOn, enableSound } = useAlerts();
  const { role } = useSession();
  const phone = usePhone();
  const [skipped, setSkipped] = useState(false);
  if (!phone || soundOn || skipped || !(role === "shift_manager" || role === "manager")) return <>{children}</>;
  return (
    <div className="gate">
      <div className="gate-card">
        <h1>Turn on alerts before service.</h1>
        <p className="muted">New orders, allergies and calls for help ring on this phone. You'll hear a test chime.</p>
        <button className="btn primary big" onClick={() => void enableSound()}>Turn on alerts</button>
        <button className="btn ghost small" onClick={() => setSkipped(true)}>Not now</button>
      </div>
    </div>
  );
}
