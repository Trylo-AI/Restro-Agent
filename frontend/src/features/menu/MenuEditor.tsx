// Menu edit / new item (manager+) and CSV import (owner): docs/console.md#menu.
import { useQueryClient } from "@tanstack/react-query";
import { Download, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useToasts } from "../../app/toasts";
import { Dialog, Sheet } from "../../components/ui";
import { ApiError } from "../../lib/api/client";
import { useItemAllergens, type AllergenState } from "../../lib/data";
import { menu, type MenuItemInput } from "../../lib/mutations";
import { useSession } from "../../lib/session";
import type { MenuItem } from "../../lib/types";

export const ALLERGENS = [
  ["gluten", "Gluten"], ["milk", "Milk"], ["eggs", "Eggs"], ["peanuts", "Peanuts"], ["tree_nuts", "Tree nuts"],
  ["soy", "Soy"], ["sesame", "Sesame"], ["fish", "Fish"], ["shellfish", "Shellfish"], ["mustard", "Mustard"],
  ["sulphites", "Sulphites"],
] as const;
const STATES: [AllergenState, string][] = [["contains", "Contains"], ["free_from", "Free from"], ["unknown", "Not sure"]];

/** Rupees as typed → integer paise, or null when it isn't a valid price (at most 2 decimals, more than 0). */
export function toPaise(rupees: string): number | null {
  const t = rupees.replace(/[₹,\s]/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
  const [whole, frac = ""] = t.split(".");
  const paise = Number(whole) * 100 + Number(frac.padEnd(2, "0"));
  return paise > 0 ? paise : null;
}

export function ItemDialog({ item, categories, onClose }: { item: MenuItem | null; categories: string[]; onClose: () => void }) {
  const toasts = useToasts();
  const qc = useQueryClient();
  const { cafeId } = useSession();
  const current = useItemAllergens(item?.id ?? null);
  const [name, setName] = useState(item?.name ?? "");
  const [description, setDescription] = useState(item?.description ?? "");
  const [category, setCategory] = useState(item?.category ?? "");
  const [price, setPrice] = useState(item ? (item.price_paise / 100).toString() : "");
  const [prep, setPrep] = useState(item ? String(item.prep_minutes) : "5");
  const [veg, setVeg] = useState(item?.veg ?? true);
  const [spice, setSpice] = useState(item?.spice ?? 0);
  const [allergens, setAllergens] = useState<Record<string, AllergenState>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  useEffect(() => { if (current.data) setAllergens(current.data); }, [current.data]);

  const paise = toPaise(price);
  const prepN = Number(prep);
  const valid = name.trim().length >= 1 && name.trim().length <= 60 && category.trim().length >= 1 && paise !== null
    && Number.isInteger(prepN) && prepN >= 1 && prepN <= 120 && description.length <= 200;

  async function save() {
    setBusy(true);
    setError(null);
    const body: MenuItemInput = {
      name: name.trim(), description: description.trim() || null, category: category.trim(), price_paise: paise!,
      prep_minutes: prepN, veg, spice, available: item?.available ?? true,
      allergens: ALLERGENS.map(([a]) => ({ allergen: a, state: allergens[a] ?? "unknown" })),
    };
    try {
      if (item) await menu.update(item.id, body, item.version);
      else await menu.create(body);
      toasts.show(item ? `${body.name} saved · the agent uses it from the next message` : `${body.name} added to the menu`);
      void qc.invalidateQueries({ queryKey: ["menu", cafeId] });
      void qc.invalidateQueries({ queryKey: ["menu-allergens", cafeId] });
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.code === "version_conflict") setStale(true);
      setError(e instanceof Error ? e.message : "Couldn't save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet title={item ? `Edit ${item.name}` : "New item"} onClose={onClose}>
      <form className="item-form" onSubmit={(e) => { e.preventDefault(); if (valid && !busy) void save(); }}>
        <label className="field">Name<input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} autoFocus={!item} /></label>
        <label className="field">Description<textarea rows={2} maxLength={200} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
        <label className="field">Category
          <input list="menu-categories" value={category} maxLength={40} onChange={(e) => setCategory(e.target.value)} />
          <datalist id="menu-categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
        </label>
        <div className="row two">
          <label className="field">Price (₹, incl. GST)<input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} aria-invalid={price !== "" && paise === null} /></label>
          <label className="field">Prep time (min)<input inputMode="numeric" value={prep} onChange={(e) => setPrep(e.target.value.replace(/\D/g, ""))} aria-invalid={!(prepN >= 1 && prepN <= 120)} /></label>
        </div>
        <div className="row two">
          <div className="field">Type
            <div className="segmented" role="radiogroup" aria-label="Veg or non-veg">
              <button type="button" role="radio" aria-checked={veg} className={veg ? "on" : ""} onClick={() => setVeg(true)}>Veg</button>
              <button type="button" role="radio" aria-checked={!veg} className={!veg ? "on" : ""} onClick={() => setVeg(false)}>Non-veg</button>
            </div>
          </div>
          <div className="field">Spice
            <div className="segmented" role="radiogroup" aria-label="Spice">
              {[0, 1, 2, 3].map((s) => <button key={s} type="button" role="radio" aria-checked={spice === s} className={spice === s ? "on" : ""} onClick={() => setSpice(s)}>{s === 0 ? "None" : "🌶".repeat(s)}</button>)}
            </div>
          </div>
        </div>
        <fieldset className="allergen-grid">
          <legend>Allergens</legend>
          <p className="hint">Not sure means the agent always passes allergen questions about this dish to a person.</p>
          {current.isPending && item ? <p className="muted">Loading allergens…</p> : ALLERGENS.map(([a, label]) => (
            <div key={a} className="allergen-row">
              <span>{label}</span>
              <div className="segmented small" role="radiogroup" aria-label={label}>
                {STATES.map(([s, l]) => (
                  <button key={s} type="button" role="radio" aria-checked={(allergens[a] ?? "unknown") === s}
                    className={(allergens[a] ?? "unknown") === s ? "on" : ""} onClick={() => setAllergens({ ...allergens, [a]: s })}>{l}</button>
                ))}
              </div>
            </div>
          ))}
        </fieldset>
        {error && <div className="error-text">{stale ? "Someone else changed this item." : error}
          {stale && <button type="button" className="btn small" onClick={() => { void qc.invalidateQueries({ queryKey: ["menu", cafeId] }); onClose(); }}>Reload</button>}</div>}
        <div className="row sheet-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!valid || busy || stale}>{item ? "Save" : "Add item"}</button>
        </div>
      </form>
    </Sheet>
  );
}

const TEMPLATE = "name,category,price,prep_minutes,veg,description,spice,contains,free_from,available\n"
  + "Cold Coffee,Cold coffee,190,5,yes,Iced and creamy,0,milk,gluten;eggs,yes\n";

interface RowError { row: number; column: string; message: string }

export function ImportDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toasts = useToasts();
  const { cafeId } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ created: number; updated: number } | null>(null);
  const [errors, setErrors] = useState<RowError[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  const template = useMemo(() => URL.createObjectURL(new Blob([TEMPLATE], { type: "text/csv" })), []);
  useEffect(() => () => URL.revokeObjectURL(template), [template]);

  async function upload() {
    if (!file) return;
    setBusy(true);
    setErrors([]);
    setMessage(null);
    try {
      if (file.size > 1_000_000) throw new Error("The file is larger than 1 MB.");
      const r = await menu.importCsv(await file.text());
      setResult(r);
      toasts.show(`${r.created} created · ${r.updated} updated`);
      void qc.invalidateQueries({ queryKey: ["menu", cafeId] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Couldn't import");
      if (e instanceof ApiError) setErrors((e.details?.rows as RowError[] | undefined) ?? []);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title="Import menu from CSV" onClose={onClose} wide footer={
      <><button className="btn" onClick={onClose}>{result ? "Done" : "Cancel"}</button>
        {!result && <button className="btn primary" disabled={!file || busy} onClick={() => void upload()}><Upload size={15} /> Import</button>}</>
    }>
      <p>All or nothing: if any row has a problem, nothing changes. Rows match dishes by name: matches are updated, new names are added, dishes not in the file stay as they are.</p>
      <p><a className="btn small" href={template} download="restro-menu-template.csv"><Download size={15} /> Download template</a></p>
      <p className="hint">Allergens: list ids in <code>contains</code> and <code>free_from</code> separated by <code>;</code> (gluten, milk, eggs, peanuts, tree_nuts, soy, sesame, fish, shellfish, mustard, sulphites). Unlisted allergens are “Not sure”.</p>
      <label className="field">CSV file<input type="file" accept=".csv,text/csv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); setErrors([]); setMessage(null); }} /></label>
      {result && <p className="ok-text">{result.created} created · {result.updated} updated</p>}
      {message && <p className="error-text">{message}</p>}
      {errors.length > 0 && (
        <table className="table">
          <thead><tr><th>Row</th><th>Column</th><th>Problem</th></tr></thead>
          <tbody>{errors.map((e, i) => <tr key={i}><td>{e.row}</td><td>{e.column || "—"}</td><td>{e.message}</td></tr>)}</tbody>
        </table>
      )}
    </Dialog>
  );
}
