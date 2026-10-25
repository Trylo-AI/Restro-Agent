// Setup (owner): Tables & QR · Team · Settings · Alerts, each tab with its own URL (docs/console.md#setup).
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Printer, Volume2 } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, NavLink, useParams } from "react-router-dom";

import { useAlerts } from "../../app/alerts";
import { useOffline } from "../../app/App";
import { useToasts } from "../../app/toasts";
import { Dialog, Empty } from "../../components/ui";
import { useCafe, useTableList, useTeam, type TableRow, type TeamMember } from "../../lib/data";
import { setup, type Role } from "../../lib/mutations";
import { atLeast, useSession } from "../../lib/session";
import { play } from "../../lib/sound";

export const COUNTER_QR_TEXT = "Hi, send me offers"; // backend/app/domain/feedback COUNTER_QR_TEXT
const TABS = [["tables", "Tables & QR"], ["team", "Team"], ["settings", "Settings"], ["alerts", "Alerts"]] as const;
const ROLE: Record<Role, string> = { shift_manager: "Shift manager", manager: "Manager", owner: "Owner" };

export function waLink(number: string | null | undefined, text: string): string | null {
  const digits = (number ?? "").replace(/\D/g, "");
  return digits ? `https://wa.me/${digits}?text=${encodeURIComponent(text)}` : null;
}

export function Qr({ value, size = 160, label }: { value: string; size?: number; label: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void QRCode.toDataURL(value, { margin: 1, width: size * 2, errorCorrectionLevel: "M" }).then((url) => live && setSrc(url));
    return () => { live = false; };
  }, [value, size]);
  return src ? <img src={src} width={size} height={size} alt={label} className="qr" /> : <span className="qr placeholder" style={{ width: size, height: size }} />;
}

function useSave() {
  const toasts = useToasts();
  const qc = useQueryClient();
  return async (fn: () => Promise<unknown>, done: string, keys: string[][]): Promise<boolean> => {
    try {
      await fn();
      toasts.show(done);
      return true;
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't save", true);
      return false;
    } finally {
      for (const k of keys) void qc.invalidateQueries({ queryKey: k });
    }
  };
}

export function SetupScreen() {
  const { tab = "tables" } = useParams();
  const { role } = useSession();
  if (!atLeast(role, "owner")) return <Navigate to="/orders" replace />;
  if (!TABS.some(([t]) => t === tab)) return <Navigate to="/setup/tables" replace />;
  return (
    <div className="setup">
      <nav className="tabs-row" aria-label="Setup">
        {TABS.map(([t, label]) => <NavLink key={t} to={`/setup/${t}`} className={({ isActive }) => `chip-btn${isActive ? " on" : ""}`}>{label}</NavLink>)}
      </nav>
      {tab === "tables" && <TablesTab />}
      {tab === "team" && <TeamTab />}
      {tab === "settings" && <SettingsTab />}
      {tab === "alerts" && <AlertsTab />}
    </div>
  );
}

// ── Tables & QR ───────────────────────────────────────────────────────────

function TablesTab() {
  const tables = useTableList();
  const cafe = useCafe();
  const [editing, setEditing] = useState<TableRow | "new" | null>(null);
  const number = cafe.data?.display_phone_number;
  return (
    <section>
      <div className="row toolbar">
        <p className="grow muted">Each table's QR opens WhatsApp with its own message, so orders land on the right table.</p>
        <Link className="btn small" to="/setup/qr-sheet" target="_blank"><Printer size={15} /> Print all QR codes</Link>
        <button className="btn small primary" onClick={() => setEditing("new")}><Plus size={15} /> Add table</button>
      </div>
      {!number && <div className="banner warning">No WhatsApp number on this café yet: QR codes appear once the Restro team connects it.</div>}
      {tables.isPending ? <Empty>Loading tables…</Empty> : (
        <ul className="qr-grid">
          {(tables.data ?? []).map((t) => {
            const link = waLink(number, t.qr_text);
            return (
              <li key={t.id} className="qr-card">
                {link ? <Qr value={link} size={112} label={`QR for ${t.label}`} /> : <span className="qr placeholder" />}
                <div className="grow">
                  <strong>{t.label}</strong>
                  <div className="muted small">{[t.area, t.seats ? `${t.seats} seats` : null].filter(Boolean).join(" · ") || "—"}</div>
                  <div className="small mono">“{t.qr_text}”</div>
                </div>
                <button className="btn small ghost" aria-label={`Edit ${t.label}`} onClick={() => setEditing(t)}><Pencil size={15} /></button>
              </li>
            );
          })}
          <li className="qr-card counter">
            {waLink(number, COUNTER_QR_TEXT) ? <Qr value={waLink(number, COUNTER_QR_TEXT)!} size={112} label="Counter QR for offers" /> : <span className="qr placeholder" />}
            <div className="grow"><strong>Counter QR</strong><div className="muted small">Customers scan it to ask for offers (they still confirm on WhatsApp).</div></div>
          </li>
        </ul>
      )}
      {editing && <TableDialog table={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </section>
  );
}

function TableDialog({ table, onClose }: { table: TableRow | null; onClose: () => void }) {
  const save = useSave();
  const { cafeId } = useSession();
  const [label, setLabel] = useState(table?.label ?? "");
  const [area, setArea] = useState(table?.area ?? "");
  const [seats, setSeats] = useState(table?.seats ? String(table.seats) : "");
  const [busy, setBusy] = useState(false);
  const seatsN = seats ? Number(seats) : null;
  const valid = label.trim().length >= 1 && label.trim().length <= 20 && (seatsN === null || (seatsN >= 1 && seatsN <= 50));
  const body = { label: label.trim(), area: area.trim() || null, seats: seatsN };
  return (
    <Dialog title={table ? `Edit ${table.label}` : "Add table"} onClose={onClose} footer={
      <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!valid || busy} onClick={async () => {
          setBusy(true);
          const ok = await save(() => (table ? setup.updateTable(table.id, body) : setup.createTable(body)),
            table ? `${body.label} saved` : `${body.label} added`, [["table-list", cafeId!], ["tables", cafeId!]]);
          setBusy(false);
          if (ok) onClose();
        }}>{table ? "Save" : "Add table"}</button></>
    }>
      <label className="field">Label<input value={label} maxLength={20} onChange={(e) => setLabel(e.target.value)} placeholder="T12" autoFocus /></label>
      <label className="field">Area<input value={area} maxLength={40} onChange={(e) => setArea(e.target.value)} placeholder="Patio" /></label>
      <label className="field">Seats<input inputMode="numeric" value={seats} onChange={(e) => setSeats(e.target.value.replace(/\D/g, ""))} /></label>
      {table && label.trim() !== table.label && <p className="hint warning-text">Changing the label changes this table's QR: reprint it, the old one stops working.</p>}
    </Dialog>
  );
}

/** A print-ready sheet of every table's QR plus the counter QR (the browser prints it to PDF). */
export function QrSheet() {
  const tables = useTableList();
  const cafe = useCafe();
  const number = cafe.data?.display_phone_number ?? "";
  const display = number ? `+${number.replace(/\D/g, "")}` : "our WhatsApp number";
  const cards: { title: string; text: string; scan: string }[] = [
    ...(tables.data ?? []).map((t) => ({ title: `Table ${t.label.replace(/^T(\d+)$/i, "$1")}`, text: t.qr_text, scan: "Scan to order on WhatsApp" })),
    { title: "Offers", text: COUNTER_QR_TEXT, scan: "Scan to get our offers on WhatsApp" },
  ];
  return (
    <div className="qr-sheet">
      <div className="no-print row"><button className="btn primary" onClick={() => window.print()}><Printer size={15} /> Print</button>
        <span className="muted">Print or save as PDF. One card per table.</span></div>
      {!number ? <Empty>No WhatsApp number on this café yet.</Empty> : (
        <div className="sheet-grid">
          {cards.map((c) => (
            <div key={c.text} className="print-card">
              <div className="cafe">{cafe.data?.name}</div>
              <div className="title">{c.title}</div>
              <Qr value={waLink(number, c.text)!} size={180} label={`QR: ${c.title}`} />
              <div className="scan">{c.scan}</div>
              <div className="fallback">Can't scan? Send “{c.text}” to {display} on WhatsApp, or ask a waiter.</div>
              <div className="fallback">Can't order on WhatsApp? Ask any of our team.</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Team ──────────────────────────────────────────────────────────────────

function TeamTab() {
  const team = useTeam();
  const save = useSave();
  const { cafeId, userId } = useSession();
  const offline = useOffline();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("shift_manager");
  const [busy, setBusy] = useState(false);
  const emailOk = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim());
  const keys = [["team", cafeId!]];
  const owners = (team.data ?? []).filter((m) => m.role === "owner" && m.active).length;

  async function invite() {
    setBusy(true);
    if (await save(() => setup.invite(email.trim(), role, name.trim() || null), `Invite sent to ${email.trim()}`, keys)) {
      setEmail("");
      setName("");
    }
    setBusy(false);
  }

  const row = (m: TeamMember): ReactNode => {
    const lastOwner = m.role === "owner" && m.active && owners <= 1;
    return (
      <tr key={m.id} className={m.active ? "" : "inactive"}>
        <td>{m.display_name ?? "—"}{m.user_id === userId ? <span className="muted small"> (you)</span> : null}</td>
        <td>
          <select aria-label={`Role of ${m.display_name ?? "member"}`} value={m.role} disabled={offline || lastOwner}
            onChange={(e) => void save(() => setup.updateMember(m.id, { role: e.target.value as Role }), "Role changed", keys)}>
            {(Object.keys(ROLE) as Role[]).map((r) => <option key={r} value={r}>{ROLE[r]}</option>)}
          </select>
        </td>
        <td>{m.active ? <span className="chip ok">Active</span> : <span className="chip calm">Deactivated</span>}</td>
        <td>{m.role === "owner" ? <span className="muted small">{lastOwner ? "Last owner" : "Owners stay active"}</span> : (
          <button className="btn small" disabled={offline} onClick={() => void save(() => setup.updateMember(m.id, { active: !m.active }),
            m.active ? "Deactivated · they're signed out on their next action" : "Reactivated", keys)}>{m.active ? "Deactivate" : "Reactivate"}</button>
        )}</td>
      </tr>
    );
  };

  return (
    <section>
      <form className="invite row" onSubmit={(e) => { e.preventDefault(); if (emailOk && !busy) void invite(); }}>
        <label className="field grow">Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={200} /></label>
        <label className="field">Name<input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} placeholder="Ravi" /></label>
        <label className="field">Role
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>{(Object.keys(ROLE) as Role[]).map((r) => <option key={r} value={r}>{ROLE[r]}</option>)}</select>
        </label>
        <button className="btn primary" disabled={!emailOk || busy || offline}>Invite</button>
      </form>
      <p className="hint">One login per person. They get an email from Supabase to set a password.</p>
      {team.isPending ? <Empty>Loading team…</Empty> : (
        <table className="table team">
          <thead><tr><th>Name</th><th>Role</th><th>Status</th><th /></tr></thead>
          <tbody>{(team.data ?? []).map(row)}</tbody>
        </table>
      )}
    </section>
  );
}

// ── Settings ──────────────────────────────────────────────────────────────

const DAYS = [["mon", "Monday"], ["tue", "Tuesday"], ["wed", "Wednesday"], ["thu", "Thursday"], ["fri", "Friday"], ["sat", "Saturday"], ["sun", "Sunday"]] as const;
type Hours = Record<string, [string, string][]>;

function SettingsTab() {
  const cafe = useCafe();
  const save = useSave();
  const { cafeId } = useSession();
  const offline = useOffline();
  const [hours, setHours] = useState<Hours | null>(null);
  const [cutoff, setCutoff] = useState("30");
  const [tone, setTone] = useState<"warm" | "crisp" | "playful">("warm");
  const [delay, setDelay] = useState<15 | 30 | 60>(30);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!cafe.data || hours) return;
    setHours(cafe.data.opening_hours ?? {});
    setCutoff(String(cafe.data.cutoff_minutes ?? 30));
    setTone((cafe.data.settings?.tone as "warm") ?? "warm");
    setDelay((cafe.data.settings?.feedback_delay_minutes as 30) ?? 30);
  }, [cafe.data, hours]);

  if (!hours) return <Empty>Loading settings…</Empty>;
  const always = Object.keys(hours).length === 0;
  const cutoffN = Number(cutoff);
  const valid = Number.isInteger(cutoffN) && cutoffN >= 0 && cutoffN <= 180;
  const setDay = (day: string, w: [string, string][]) => setHours({ ...hours, [day]: w });

  return (
    <section className="settings">
      <fieldset className="hours">
        <legend>Opening hours</legend>
        <label className="row small"><input type="checkbox" checked={always} onChange={(e) => setHours(e.target.checked ? {} : Object.fromEntries(DAYS.map(([d]) => [d, [["08:00", "23:00"]]])))} /> Always open</label>
        {!always && DAYS.map(([d, label]) => {
          const w = hours[d] ?? [];
          const open = w.length > 0;
          return (
            <div key={d} className="hours-row">
              <label className="day"><input type="checkbox" checked={open} onChange={(e) => setDay(d, e.target.checked ? [["08:00", "23:00"]] : [])} /> {label}</label>
              {open ? <>
                <input type="time" aria-label={`${label} opens`} value={w[0]![0]} onChange={(e) => setDay(d, [[e.target.value, w[0]![1]]])} />
                <span>–</span>
                <input type="time" aria-label={`${label} closes`} value={w[0]![1]} onChange={(e) => setDay(d, [[w[0]![0], e.target.value]])} />
                {w[0]![1] < w[0]![0] && <span className="muted small">closes after midnight</span>}
              </> : <span className="muted">Closed</span>}
            </div>
          );
        })}
      </fieldset>
      <label className="field">Last orders (minutes before closing)<input inputMode="numeric" value={cutoff} onChange={(e) => setCutoff(e.target.value.replace(/\D/g, ""))} /></label>
      <div className="field">Agent tone
        <div className="segmented" role="radiogroup" aria-label="Agent tone">
          {(["warm", "crisp", "playful"] as const).map((t) => <button key={t} type="button" role="radio" aria-checked={tone === t} className={tone === t ? "on" : ""} onClick={() => setTone(t)}>{t[0]!.toUpperCase() + t.slice(1)}</button>)}
        </div>
      </div>
      <div className="field">Ask for feedback after serving
        <div className="segmented" role="radiogroup" aria-label="Feedback delay">
          {([15, 30, 60] as const).map((m) => <button key={m} type="button" role="radio" aria-checked={delay === m} className={delay === m ? "on" : ""} onClick={() => setDelay(m)}>{m} min</button>)}
        </div>
      </div>
      <p className="hint">Menu prices include GST; there's no separate tax setting.</p>
      <button className="btn primary" disabled={!valid || busy || offline} onClick={async () => {
        setBusy(true);
        await save(() => setup.saveSettings(cafeId!, { opening_hours: hours, cutoff_minutes: cutoffN, tone, feedback_delay_minutes: delay }),
          "Settings saved", [["cafe", cafeId!]]);
        setBusy(false);
      }}>Save settings</button>
    </section>
  );
}

// ── Alerts ────────────────────────────────────────────────────────────────

function AlertsTab() {
  const cafe = useCafe();
  const save = useSave();
  const { cafeId } = useSession();
  const { soundOn, enableSound } = useAlerts();
  const [phone, setPhone] = useState<string | null>(null);
  useEffect(() => { if (cafe.data && phone === null) setPhone(cafe.data.settings?.alert_phone ? `+${cafe.data.settings.alert_phone}` : ""); }, [cafe.data, phone]);
  const digits = (phone ?? "").replace(/[\s-]/g, "");
  const valid = /^\+?\d{10,15}$/.test(digits);
  return (
    <section className="settings">
      <label className="field">Café alert number (WhatsApp)
        <input inputMode="tel" value={phone ?? ""} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" aria-invalid={!!phone && !valid} />
      </label>
      <p className="hint">Gets "new order not accepted in 60 s" and "call open for 2 min" alerts as a WhatsApp backup.</p>
      {phone && !valid && <p className="error-text">Use the full number with country code, 10–15 digits.</p>}
      <button className="btn primary" disabled={!valid} onClick={() => void save(() => setup.saveSettings(cafeId!, { alert_phone: digits }), "Alert number saved", [["cafe", cafeId!]])}>Save number</button>
      <h3>Sound test on this device</h3>
      {!soundOn && <button className="btn" onClick={() => void enableSound()}><Volume2 size={15} /> Turn on sound</button>}
      <div className="row wrap">
        <button className="btn small" disabled={!soundOn} onClick={() => play("chime")}>Calm · chime</button>
        <button className="btn small" disabled={!soundOn} onClick={() => play("two_note")}>Warning · two notes</button>
        <button className="btn small" disabled={!soundOn} onClick={() => play("alarm")}>Critical · alarm</button>
      </div>
    </section>
  );
}
