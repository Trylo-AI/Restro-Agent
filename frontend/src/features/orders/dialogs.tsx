// Delay panel, Cancel dialog, Manual order (docs/console.md#live-orders).
import { Minus, Plus } from "lucide-react";
import { useMemo, useState } from "react";

import { useToasts } from "../../app/toasts";
import { Dialog } from "../../components/ui";
import { useMenu, useTables, type LiveOrder } from "../../lib/data";
import { inr } from "../../lib/format";
import { newIdempotencyKey, orders, type CancelReason } from "../../lib/mutations";

export function DelayDialog({ order, onClose, onSubmit }: {
  order: LiveOrder; onClose: () => void; onSubmit: (minutes: number, note: string | null) => Promise<boolean>;
}) {
  const [preset, setPreset] = useState<number | "custom">(5);
  const [custom, setCustom] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const minutes = preset === "custom" ? Number(custom) : preset;
  const valid = Number.isInteger(minutes) && minutes >= 1 && minutes <= 99 && note.length <= 120;
  // Same text as backend domain/orders/service.py delay().
  const preview = `Sorry for the wait — your order needs about ${valid ? minutes : "N"} more minutes.${note.trim() ? ` ${note.trim()}` : ""}`;

  return (
    <Dialog title={`Delay #${order.number} · ${order.table ?? ""}`} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Back</button>
        <button className="btn primary" disabled={!valid || busy} onClick={async () => {
          setBusy(true);
          if (await onSubmit(minutes, note.trim() || null)) onClose();
          setBusy(false);
        }}>Delay and tell the customer</button>
      </>
    }>
      <div className="segmented" role="radiogroup" aria-label="Delay by">
        {([2, 5, 10] as const).map((m) => (
          <button key={m} role="radio" aria-checked={preset === m} className={preset === m ? "on" : ""} onClick={() => setPreset(m)}>+{m}</button>
        ))}
        <button role="radio" aria-checked={preset === "custom"} className={preset === "custom" ? "on" : ""} onClick={() => setPreset("custom")}>Custom</button>
      </div>
      {preset === "custom" && (
        <label className="field">Minutes (1–99)
          <input type="number" min={1} max={99} inputMode="numeric" value={custom} onChange={(e) => setCustom(e.target.value)} autoFocus />
        </label>
      )}
      <label className="field">Note for the customer (optional)
        <input maxLength={120} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Fresh batch of dosa batter coming up." />
      </label>
      <div className="preview"><div className="muted small-caps">Customer sees</div>{preview}</div>
    </Dialog>
  );
}

const REASONS: { code: CancelReason; label: string }[] = [
  { code: "out_of_stock", label: "Out of stock" },
  { code: "kitchen_issue", label: "Kitchen issue" },
  { code: "customer_asked", label: "Customer asked" },
  { code: "other", label: "Other" },
];

export function CancelDialog({ order, onClose, onSubmit }: {
  order: LiveOrder; onClose: () => void; onSubmit: (reason: CancelReason, text: string | null) => Promise<boolean>;
}) {
  const [reason, setReason] = useState<CancelReason | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = reason !== null && (reason !== "other" || text.trim().length > 0) && text.length <= 120;

  return (
    <Dialog title={`Cancel #${order.number} · ${order.table ?? ""}`} onClose={onClose} footer={
      <>
        <button className="btn" onClick={onClose}>Keep order</button>
        <button className="btn danger" disabled={!valid || busy} onClick={async () => {
          setBusy(true);
          if (await onSubmit(reason!, reason === "other" ? text.trim() : text.trim() || null)) onClose();
          setBusy(false);
        }}>Cancel order</button>
      </>
    }>
      <fieldset className="reasons">
        <legend>Reason</legend>
        {REASONS.map((r) => (
          <label key={r.code} className={`reason${reason === r.code ? " on" : ""}`}>
            <input type="radio" name="reason" checked={reason === r.code} onChange={() => setReason(r.code)} /> {r.label}
          </label>
        ))}
      </fieldset>
      {reason === "other" && (
        <label className="field">What happened (required)
          <input maxLength={120} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
        </label>
      )}
      <p className="hint">The customer gets an apology. Your own text stays with the team.</p>
    </Dialog>
  );
}

export function ManualOrderDialog({ onClose }: { onClose: () => void }) {
  const tables = useTables();
  const menu = useMenu();
  const toasts = useToasts();
  const [key] = useState(newIdempotencyKey);
  const [tableId, setTableId] = useState("");
  const [qty, setQty] = useState<Record<string, number>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [allergy, setAllergy] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const available = useMemo(() => (menu.data ?? []).filter((m) => m.available), [menu.data]);
  const categories = useMemo(() => [...new Set(available.map((m) => m.category))], [available]);
  const lines = Object.entries(qty).filter(([, q]) => q > 0);
  const bump = (id: string, d: number) => setQty((q) => ({ ...q, [id]: Math.max(0, Math.min(20, (q[id] ?? 0) + d)) }));

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const o = await orders.manual(key, tableId,
        lines.map(([item_id, n]) => ({ item_id, qty: n, note: notes[item_id]?.trim() || null })), allergy.trim() || null);
      toasts.show(`Manual order #${o.number} added`);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't add the order. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog wide title="Add manual order" onClose={onClose} footer={
      <>
        <span className="muted grow">{lines.length ? `${lines.reduce((s, [, n]) => s + n, 0)} items` : "No items yet"}</span>
        <button className="btn" onClick={onClose}>Close</button>
        <button className="btn primary" disabled={!tableId || !lines.length || busy} onClick={() => void submit()}>Add order</button>
      </>
    }>
      <p className="hint">For when WhatsApp is down or a guest orders at the counter. It starts as Accepted and sends no messages.</p>
      <label className="field">Table
        <select value={tableId} onChange={(e) => setTableId(e.target.value)}>
          <option value="">Choose a table</option>
          {(tables.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      </label>
      {categories.map((c) => (
        <section key={c} className="manual-cat">
          <h3>{c}</h3>
          {available.filter((m) => m.category === c).map((m) => (
            <div key={m.id} className="manual-item">
              <div className="row">
                <span className={`veg-dot${m.veg ? " veg" : ""}`} aria-label={m.veg ? "Veg" : "Non-veg"} />
                <span className="grow">{m.name} <span className="muted">{inr(m.price_paise)}</span></span>
                <button className="btn small" aria-label={`One less ${m.name}`} onClick={() => bump(m.id, -1)} disabled={!qty[m.id]}><Minus size={14} /></button>
                <span className="qty-n" aria-label={`${m.name} quantity`}>{qty[m.id] ?? 0}</span>
                <button className="btn small" aria-label={`One more ${m.name}`} onClick={() => bump(m.id, 1)}><Plus size={14} /></button>
              </div>
              {(qty[m.id] ?? 0) > 0 && (
                <input className="line-note" maxLength={120} placeholder="Note (optional)" value={notes[m.id] ?? ""}
                  onChange={(e) => setNotes((n) => ({ ...n, [m.id]: e.target.value }))} />
              )}
            </div>
          ))}
        </section>
      ))}
      <label className="field">Allergy note (optional)
        <input maxLength={200} value={allergy} onChange={(e) => setAllergy(e.target.value)} />
      </label>
      {error && <div className="error-text">{error}</div>}
    </Dialog>
  );
}
