// Menu: sold out in two taps (docs/console.md#menu). Editing, new items and import are manager+ screens outside this slice.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { useOffline } from "../../app/App";
import { useToasts } from "../../app/toasts";
import { Empty } from "../../components/ui";
import { useMenu } from "../../lib/data";
import { inr } from "../../lib/format";
import { menu } from "../../lib/mutations";
import { atLeast, useSession } from "../../lib/session";
import type { MenuItem } from "../../lib/types";

export const ARM_MS = 5_000;

export function MenuScreen() {
  const items = useMenu();
  const offline = useOffline();
  const toasts = useToasts();
  const qc = useQueryClient();
  const { role, cafeId } = useSession();
  const manager = atLeast(role, "manager");
  const [category, setCategory] = useState("All");
  const [armed, setArmed] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(null), ARM_MS);
    return () => window.clearTimeout(t);
  }, [armed]);

  const all = useMemo(() => items.data ?? [], [items.data]);
  const categories = useMemo(() => ["All", ...new Set(all.map((m) => m.category))], [all]);
  const shown = all.filter((m) => category === "All" || m.category === category);
  const soldOut = all.filter((m) => !m.available).length;

  async function set(m: MenuItem, available: boolean) {
    setArmed(null);
    setBusy(m.id);
    try {
      await menu.setAvailable(m.id, available);
      toasts.show(available ? `${m.name} is back` : `${m.name} sold out · the agent stops offering it`);
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't change it", true);
    } finally {
      setBusy(null);
      void qc.invalidateQueries({ queryKey: ["menu", cafeId] });
    }
  }

  function tap(m: MenuItem) {
    if (!m.available) return void set(m, true); // back in stock: one tap
    if (armed === m.id) return void set(m, false); // second tap commits
    setArmed(m.id); // first tap arms; another item's tap re-arms that one instead
  }

  return (
    <div className="orders">
      <div className="summary">{soldOut ? `${soldOut} item${soldOut > 1 ? "s" : ""} sold out today` : "Everything is available"}</div>
      <div className="filters row" role="group" aria-label="Category">
        {categories.map((c) => (
          <button key={c} className={`chip-btn${category === c ? " on" : ""}`} aria-pressed={category === c} onClick={() => setCategory(c)}>{c}</button>
        ))}
      </div>
      {items.isPending && <Empty>Loading menu…</Empty>}
      {items.isError && !items.data && <Empty>Couldn't load the menu. <button className="btn small" onClick={() => void items.refetch()}>Try again</button></Empty>}
      {items.data && (shown.length ? (
        <ul className="menu-list">
          {shown.map((m) => {
            const isArmed = armed === m.id;
            return (
              <li key={m.id} className={`menu-row${m.available ? "" : " out"}`}>
                <span className={`veg-dot${m.veg ? " veg" : ""}`} aria-label={m.veg ? "Veg" : "Non-veg"} />
                <div className="grow">
                  <div className="name">{m.name}</div>
                  <div className="muted small">{m.category}{manager ? ` · ${m.prep_minutes} min · ${inr(m.price_paise)}` : ""}</div>
                </div>
                <button
                  role="switch" aria-checked={m.available} disabled={offline || busy === m.id}
                  aria-label={m.available ? (isArmed ? `Tap again to mark ${m.name} sold out` : `${m.name} available`) : `${m.name} sold out — tap to bring back`}
                  className={`avail${m.available ? " on" : ""}${isArmed ? " armed" : ""}`} onClick={() => tap(m)}>
                  {m.available ? (isArmed ? "Tap again to mark sold out" : "Available") : "Sold out"}
                </button>
              </li>
            );
          })}
        </ul>
      ) : <Empty>No items in this category.</Empty>)}
    </div>
  );
}
