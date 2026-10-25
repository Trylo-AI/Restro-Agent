// The bell (docs/console.md#urgency-and-alerts): this device's sound and notification state, the WhatsApp backup,
// sound tests, and — for manager+ — in-app notifications with an unread count. Opening the panel marks all read.
import { useQueryClient } from "@tanstack/react-query";
import { Bell as BellIcon, Volume2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Sheet } from "../components/ui";
import { useCafe, useNotifications, type AppNotification } from "../lib/data";
import { relative } from "../lib/format";
import { notifications } from "../lib/mutations";
import { atLeast, useSession } from "../lib/session";
import { notifyPermission, play } from "../lib/sound";
import { useAlerts, useNow } from "./alerts";

const REASON: Record<string, string> = {
  out_of_stock: "Out of stock", kitchen_issue: "Kitchen issue", customer_asked: "Customer asked", other: "Other",
};

export function notificationText(n: AppNotification): string {
  const p = n.payload;
  const parts = [`#${p.order_number ?? "?"}${p.table ? ` · ${p.table}` : ""} cancelled by ${p.actor_name ?? "a teammate"}`];
  if (p.reason_code) parts.push(REASON[p.reason_code] ?? p.reason_code);
  return parts.join(" · ");
}

export function Bell() {
  const { role, cafeId } = useSession();
  const manager = atLeast(role, "manager");
  const list = useNotifications(manager);
  const unread = (list.data ?? []).filter((n) => !n.read_at).length;
  const [open, setOpen] = useState(false);

  return (
    <>
      <button className="btn small ghost bell" onClick={() => setOpen(true)} aria-label={unread ? `Alerts and notifications, ${unread} unread` : "Alerts and notifications"}>
        <BellIcon size={18} />{unread > 0 && <span className="badge">{unread}</span>}
      </button>
      {open && <BellPanel items={list.data ?? []} manager={manager} cafeId={cafeId} unread={unread} onClose={() => setOpen(false)} />}
    </>
  );
}

function BellPanel({ items, manager, cafeId, unread, onClose }: {
  items: AppNotification[]; manager: boolean; cafeId: string | null; unread: number; onClose: () => void;
}) {
  const { soundOn, enableSound } = useAlerts();
  const cafe = useCafe();
  const qc = useQueryClient();
  const now = useNow(30_000);
  const [permission, setPermission] = useState<string>(typeof Notification === "undefined" ? "unsupported" : Notification.permission);

  useEffect(() => {
    if (!manager || unread === 0) return;
    void notifications.ackAll().finally(() => void qc.invalidateQueries({ queryKey: ["notifications", cafeId] }));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const alertPhone = cafe.data?.settings?.alert_phone;
  return (
    <Sheet title="Alerts and notifications" onClose={onClose}>
      <section className="pblock">
        <h3>This device</h3>
        <ul className="plain">
          <li>Sound: {soundOn ? <span className="chip ok">On</span> : <><span className="chip warning">Off</span> <button className="btn small" onClick={() => void enableSound()}>Turn on</button></>}</li>
          <li>Browser notifications: <span className={`chip ${permission === "granted" ? "ok" : "calm"}`}>{permission === "granted" ? "Allowed" : permission === "denied" ? "Blocked in the browser" : permission === "unsupported" ? "Not supported" : "Not asked"}</span>
            {permission === "default" && <button className="btn small" onClick={() => void notifyPermission().then((p) => setPermission(p))}>Allow</button>}</li>
          <li>WhatsApp backup: {alertPhone ? <span className="chip ok">On</span> : <span className="chip warning">No café alert number</span>}
            <div className="muted small">Orders not accepted in 60 s and calls open for 2 min are also sent to the café alert number.</div></li>
        </ul>
        <div className="row wrap">
          <span className="muted small"><Volume2 size={14} /> Test:</span>
          <button className="btn small" disabled={!soundOn} onClick={() => play("chime")}>Calm</button>
          <button className="btn small" disabled={!soundOn} onClick={() => play("two_note")}>Warning</button>
          <button className="btn small" disabled={!soundOn} onClick={() => play("alarm")}>Critical</button>
        </div>
      </section>
      {manager && (
        <section className="pblock">
          <h3>Notifications</h3>
          {items.length ? (
            <ul className="plist">
              {items.map((n) => (
                <li key={n.id} className={n.read_at ? "" : "unread"}>
                  <div>{notificationText(n)}</div>
                  {n.payload.reason_text && <div className="muted small">“{n.payload.reason_text}”</div>}
                  <div className="muted small">{relative(n.created_at, now)}</div>
                </li>
              ))}
            </ul>
          ) : <p className="muted">No notifications yet. Cancelled orders show up here.</p>}
        </section>
      )}
    </Sheet>
  );
}
