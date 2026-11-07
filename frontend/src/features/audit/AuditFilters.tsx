// Audit log filters (docs/console.md#audit-log): the desktop filter bar, the date presets and the phone bottom sheet.
// Every control writes the URL through `onChange`; nothing here keeps filter state of its own.
import { Download, LoaderCircle, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { Sheet } from "../../components/ui";
import { useCustomerMatches, type AuditCatalog, type AuditFacets } from "../../lib/audit";
import type { TeamMember } from "../../lib/data";
import { bounds, hasEntity, PRESETS, toLocalInput, type ActorRole, type AuditFilterState, type Category, type Preset } from "./filters";
import "../../styles/audit.css";

/** Aman's three named actions (order accepted, order cancelled, chat taken over) lead the Action list. */
export const KEY_ACTIONS = ["order.accept", "order.cancel", "chat.takeover"];
const STAFF_ROLES: readonly ActorRole[] = ["shift_manager", "manager", "owner"];

export interface Option { key: string; label: string; sub?: string; count?: number; checked: boolean; toggle(): void }
export interface Group { label?: string; options: Option[] }

type Change = (patch: Partial<AuditFilterState>) => void;

const toggled = <T,>(list: T[], v: T): T[] => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

/** A dropdown of checkboxes. Zero-count options stay selectable but greyed. */
export function MultiSelect({ label, groups }: { label: string; groups: Group[] }) {
  const n = groups.reduce((sum, g) => sum + g.options.filter((o) => o.checked).length, 0);
  return (
    <details className="ms">
      <summary>{label}{n > 0 && <span className="ms-n"> ({n})</span>}</summary>
      <div className="ms-panel" role="group" aria-label={label}>
        {groups.map((g, i) => (
          <div key={g.label ?? i} className="ms-group">
            {g.label && <div className="ms-head">{g.label}</div>}
            {g.options.map((o) => (
              <label key={o.key} className={`ms-opt${o.count === 0 ? " zero" : ""}`}>
                <input type="checkbox" checked={o.checked} onChange={o.toggle} />
                <span className="grow">{o.label}{o.sub && <span className="chip calm">{o.sub}</span>}</span>
                {o.count !== undefined && <span className="ms-count">{o.count}</span>}
              </label>
            ))}
          </div>
        ))}
        {!groups.some((g) => g.options.length) && <div className="muted small">Nothing to choose yet.</div>}
      </div>
    </details>
  );
}

/** The option lists for Category, Action, Person and Role, with the counts of the current date range. */
export function useOptions(f: AuditFilterState, onChange: Change, catalog?: AuditCatalog, facets?: AuditFacets, team?: TeamMember[]) {
  return useMemo(() => {
    const actionCount = new Map((facets?.actions ?? []).map((a) => [a.action, a.count]));
    const categoryCount = new Map((facets?.categories ?? []).map((c) => [c.category, c.count]));
    const count = <K,>(m: Map<K, number>, k: K) => (facets ? m.get(k) ?? 0 : undefined);
    const roleLabel = new Map((catalog?.roles ?? []).map((r) => [r.id, r.label]));

    const categories: Group[] = [{ options: (catalog?.categories ?? []).map((c) => ({
      key: c.id, label: c.label, count: count(categoryCount, c.id), checked: f.category.includes(c.id),
      toggle: () => onChange({ category: toggled<Category>(f.category, c.id) }),
    })) }];

    const all = catalog?.actions ?? [];
    const actionOption = (a: AuditCatalog["actions"][number]): Option => ({
      key: a.action, label: a.label, count: count(actionCount, a.action), checked: f.action.includes(a.action),
      toggle: () => onChange({ action: toggled(f.action, a.action) }),
    });
    const key = KEY_ACTIONS.map((k) => all.find((a) => a.action === k)).filter((a) => !!a);
    const actions: Group[] = [{ label: "Most asked", options: key.map(actionOption) }];
    for (const c of catalog?.categories ?? []) {
      const rest = all.filter((a) => a.category === c.id && !KEY_ACTIONS.includes(a.action))
        .sort((a, b) => Number(!!b.filter_only) - Number(!!a.filter_only)); // D28a: accept / ready / serve first
      if (rest.length) actions.push({ label: c.label, options: rest.map(actionOption) });
    }

    // Person: the team (with inactive members) plus anyone the log names who has left, then support and the AI.
    const byUser = new Map<string, { name: string; role: ActorRole; inactive: boolean; count: number }>();
    for (const m of team ?? []) byUser.set(m.user_id, { name: m.display_name ?? "A teammate", role: m.role, inactive: !m.active, count: 0 });
    const pseudo = new Map<ActorRole, number>();
    for (const a of facets?.actors ?? []) {
      if (a.role === "platform_admin" || a.role === "staff_via_ai") {
        pseudo.set(a.role, (pseudo.get(a.role) ?? 0) + a.count);
      } else if (a.user_id && STAFF_ROLES.includes(a.role)) {
        const p = byUser.get(a.user_id) ?? { name: a.name, role: a.role, inactive: true, count: 0 };
        byUser.set(a.user_id, { ...p, count: p.count + a.count });
      }
    }
    const people: Option[] = [...byUser.entries()]
      .sort(([, a], [, b]) => b.count - a.count || a.name.localeCompare(b.name))
      .map(([id, p]) => ({
        key: id, label: p.name, sub: `${roleLabel.get(p.role) ?? p.role}${p.inactive ? " (inactive)" : ""}`,
        count: facets ? p.count : undefined, checked: f.actor.includes(id),
        toggle: () => onChange({ actor: toggled(f.actor, id) }),
      }));
    const others: Option[] = ([["platform_admin", "Restro support"], ["staff_via_ai", "AI (from comments)"]] as const)
      .filter(([r]) => pseudo.has(r) || f.role.includes(r))
      .map(([r, label]) => ({
        key: r, label, count: facets ? pseudo.get(r) ?? 0 : undefined, checked: f.role.includes(r),
        toggle: () => onChange({ role: toggled(f.role, r) }),
      }));

    const roles: Group[] = [{ options: (catalog?.roles ?? []).map((r) => ({
      key: r.id, label: r.label, checked: f.role.includes(r.id), toggle: () => onChange({ role: toggled(f.role, r.id) }),
    })) }];

    return { categories, actions, people: [{ options: people }, ...(others.length ? [{ options: others }] : [])], roles };
  }, [f, onChange, catalog, facets, team]);
}

/** Today · Yesterday · 7 days · 30 days · Custom (+ All time with an order, customer or chat filter). */
export function RangePresets({ f, onChange }: { f: AuditFilterState; onChange: Change }) {
  const pick = (id: Preset) => {
    if (id !== "custom") return onChange({ range: id });
    const b = bounds(f) ?? bounds({ ...f, range: "7d" })!;
    onChange({ range: "custom", from: toLocalInput(b.from), to: toLocalInput(b.to) });
  };
  return (
    <div className="audit-range">
      <div className="segmented" role="group" aria-label="Date range">
        {PRESETS.filter((p) => !p.entityOnly || hasEntity(f)).map((p) => (
          <button key={p.id} className={f.range === p.id ? "on" : ""} aria-pressed={f.range === p.id} onClick={() => pick(p.id)}>{p.label}</button>
        ))}
      </div>
      {f.range === "custom" && (
        <div className="row wrap audit-custom">
          <label className="field">From
            <input type="datetime-local" value={f.from ?? ""} onChange={(e) => onChange({ range: "custom", from: e.target.value || null })} /></label>
          <label className="field">To
            <input type="datetime-local" value={f.to ?? ""} onChange={(e) => onChange({ range: "custom", to: e.target.value || null })} /></label>
        </div>
      )}
    </div>
  );
}

function OrderNumberField({ value, onChange }: { value: number | null; onChange: Change }) {
  const [text, setText] = useState(value === null ? "" : String(value));
  const commit = () => {
    const n = Number(text.replace(/^#/, ""));
    const next = text.trim() && Number.isInteger(n) && n > 0 ? n : null;
    if (next !== value) onChange({ order: next });
  };
  return (
    <form className="audit-order" onSubmit={(e) => { e.preventDefault(); commit(); }}>
      <label className="field"><span className="sr-only">Order number</span>
        <input inputMode="numeric" placeholder="Order #" value={text} maxLength={10} onChange={(e) => setText(e.target.value)} onBlur={commit} /></label>
    </form>
  );
}

const firstName = (name: string | null) => name?.trim().split(/\s+/)[0] || "Guest";

/** Typeahead over the customer list; customers are named by first name only (D21). */
function CustomerResults({ search, onPick }: { search: string; onPick: (id: string) => void }) {
  const q = useCustomerMatches(search);
  if (q.isPending) return <div className="ms-panel muted small">Searching…</div>;
  const rows = q.data ?? [];
  return (
    <ul className="ms-panel audit-typeahead" aria-label="Matching customers">
      {rows.length ? rows.map((c) => (
        <li key={c.customer_id}><button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => onPick(c.customer_id)}>
          {firstName(c.name)} <span className="muted small">{c.visits} visit{c.visits === 1 ? "" : "s"}</span></button></li>
      )) : <li className="muted small">No customer by that name.</li>}
    </ul>
  );
}

function CustomerField({ onChange }: { onChange: Change }) {
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => {
    // One lookup 300 ms after the last key, not one per key.
    const id = window.setTimeout(() => setSearch(text.trim()), 300);
    return () => window.clearTimeout(id);
  }, [text]);
  const pick = (id: string) => {
    setText("");
    setSearch("");
    onChange({ customer: id });
  };
  return (
    <div className="audit-customer">
      <label className="field"><span className="sr-only">Customer</span>
        <input type="search" placeholder="Customer" value={text} maxLength={40} onChange={(e) => setText(e.target.value)} /></label>
      {search.length >= 2 && text.trim().length >= 2 && <CustomerResults search={search} onPick={pick} />}
    </div>
  );
}

export interface FilterProps {
  f: AuditFilterState;
  onChange: Change;
  catalog?: AuditCatalog;
  facets?: AuditFacets;
  team?: TeamMember[];
}

function Fields({ f, onChange, catalog, facets, team }: FilterProps) {
  const o = useOptions(f, onChange, catalog, facets, team);
  return (
    <>
      <MultiSelect label="Category" groups={o.categories} />
      <MultiSelect label="Action" groups={o.actions} />
      <MultiSelect label="Person" groups={o.people} />
      <MultiSelect label="Role" groups={o.roles} />
      <OrderNumberField key={String(f.order)} value={f.order} onChange={onChange} />
      <CustomerField onChange={onChange} />
    </>
  );
}

/** Desktop: one row of dropdowns under the toolbar. */
export function FilterBar(p: FilterProps) {
  return <div className="audit-filters" aria-label="Filters" role="group"><Fields {...p} /></div>;
}

/** Phone: "Filters (n)" opens a bottom sheet with every filter, the presets and Export. */
export function FilterSheetButton({ active, children }: { active: number; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  return (
    <>
      <button className="btn small" onClick={() => setOpen(true)}><SlidersHorizontal size={15} aria-hidden /> Filters{active ? ` (${active})` : ""}</button>
      {open && <Sheet title="Filters" onClose={close}>{children(close)}</Sheet>}
    </>
  );
}

export function FilterSheetBody({ exporting, onExport, onDone, ...p }: FilterProps & { exporting: boolean; onExport: () => void; onDone: () => void }) {
  return (
    <div className="audit-sheet">
      <RangePresets f={p.f} onChange={p.onChange} />
      <div className="audit-sheet-fields"><Fields {...p} /></div>
      <ExportButton busy={exporting} onClick={onExport} />
      <button className="btn primary big" onClick={onDone}>Show results</button>
    </div>
  );
}

export function ExportButton({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  return (
    <button className="btn small" disabled={busy} onClick={onClick}>
      {busy ? <LoaderCircle size={15} className="audit-spin" aria-hidden /> : <Download size={15} aria-hidden />} Export CSV
    </button>
  );
}
