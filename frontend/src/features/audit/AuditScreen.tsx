// Audit log: who did what in this café (docs/console.md#audit-log). Owner only (D15). Reads through the API (labels,
// sentences and severity reasons come from the backend catalogue); every filter lives in the URL; refetches every
// 60 s and on focus, no Realtime (D26).
import { CircleCheck, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";

import { useToasts } from "../../app/toasts";
import { Empty } from "../../components/ui";
import { ApiError } from "../../lib/api/client";
import { exportAuditCsv, useAuditCatalog, useAuditEntries, useAuditFacets, type AuditEntry } from "../../lib/audit";
import { useCustomerProfileRow, useTeam } from "../../lib/data";
import { usePhone } from "../../lib/media";
import { atLeast, useSession } from "../../lib/session";
import { ExportButton, FilterBar, FilterSheetBody, FilterSheetButton, RangePresets } from "./AuditFilters";
import { AuditRow } from "./AuditRow";
import {
  activeChips, EMPTY, facetBounds, isFiltered, parseFilters, patchFilters, rangeDays, rangePhrase, toQuery, toSearch,
  type AuditFilterState, type ChipLabels,
} from "./filters";
import "../../styles/audit.css";

export function AuditScreen() {
  const { role } = useSession();
  if (!atLeast(role, "owner")) return <Navigate to="/orders" replace />;
  return <AuditLog />;
}

const DAY_HEAD = new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short" });
const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

/** "Today", "Yesterday" or "Sat, 25 Oct", in the browser's local days. */
function dayHeading(iso: string, now = new Date()): string {
  const d = new Date(iso);
  if (dayKey(d) === dayKey(now)) return "Today";
  if (dayKey(d) === dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1))) return "Yesterday";
  return DAY_HEAD.format(d);
}

function byDay(entries: AuditEntry[]): { day: string; entries: AuditEntry[] }[] {
  const out: { day: string; entries: AuditEntry[] }[] = [];
  for (const e of entries) {
    const day = dayHeading(e.at);
    const last = out[out.length - 1];
    if (last?.day === day) last.entries.push(e);
    else out.push({ day, entries: [e] });
  }
  return out;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The shell header is sticky too: the day headings stick just below it, whatever its height (title, phone layout). */
function useStickyTop() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    const header = document.querySelector<HTMLElement>(".header");
    if (!el || !header || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => el.style.setProperty("--audit-sticky-top", `${header.offsetHeight}px`));
    ro.observe(header);
    return () => ro.disconnect();
  }, []);
  return ref;
}

function AuditLog() {
  const [sp, setSp] = useSearchParams();
  const f = useMemo(() => parseFilters(sp), [sp]);
  const change = useCallback((patch: Partial<AuditFilterState>) => setSp(toSearch(patchFilters(f, patch))), [f, setSp]);
  const replace = useCallback((next: AuditFilterState) => setSp(toSearch(next)), [setSp]);
  const query = useMemo(() => toQuery(f), [f]);
  const fb = useMemo(() => facetBounds(f), [f]);
  const phone = usePhone();
  const toasts = useToasts();
  const [exporting, setExporting] = useState(false);
  const stickyRef = useStickyTop();

  const entries = useAuditEntries(query);
  const facets = useAuditFacets(fb.from.toISOString(), fb.to.toISOString());
  const catalog = useAuditCatalog();
  const team = useTeam();
  const customerRow = useCustomerProfileRow(f.customer);

  const rows = useMemo(() => entries.data?.pages.flatMap((p) => p.entries) ?? [], [entries.data]);
  const labels = useMemo(() => {
    const c = catalog.data;
    const action = new Map((c?.actions ?? []).map((a) => [a.action, a.label]));
    const category = new Map((c?.categories ?? []).map((x) => [x.id, x.label]));
    const role = new Map((c?.roles ?? []).map((x) => [x.id, x.label]));
    const people = new Map<string, string>();
    for (const a of facets.data?.actors ?? []) if (a.user_id) people.set(a.user_id, a.name);
    for (const m of team.data ?? []) people.set(m.user_id, m.display_name ?? "A teammate");
    const order = rows.find((e) => e.order?.id === f.order_id)?.order;
    const customerName = customerRow.data?.name ?? rows.find((e) => e.customer?.id === f.customer)?.customer?.name;
    const l: ChipLabels = {
      action: (a) => action.get(a) ?? a,
      category: (x) => category.get(x) ?? x,
      role: (x) => role.get(x) ?? x,
      actor: (id) => people.get(id) ?? "A team member",
      customer: () => customerName?.trim().split(/\s+/)[0] ?? "This customer",
      orderId: () => (order ? `Order #${order.number}` : "This order"),
    };
    return { ...l, categoryOf: (x: string) => category.get(x as never) ?? x, roleOf: (x: string) => role.get(x as never) ?? x };
  }, [catalog.data, facets.data, team.data, rows, customerRow.data, f.order_id, f.customer]);
  const chips = activeChips(f, labels);

  async function doExport() {
    setExporting(true);
    try {
      const n = await exportAuditCsv(query);
      toasts.show(`Audit log exported (${plural(n, "row")})`);
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't export", true);
    } finally {
      setExporting(false);
    }
  }

  const sev = facets.data?.by_severity;
  const critical = sev?.critical ?? 0;
  const filterProps = { f, onChange: change, catalog: catalog.data, facets: facets.data, team: team.data };

  return (
    <div className="audit" ref={stickyRef}>
      <div className="audit-toolbar">
        {!phone && <RangePresets f={f} onChange={change} />}
        <SearchBox value={f.q} onCommit={(q) => change({ q })} />
        <div className="row">
          <button className={`audit-switch${f.critical ? " on" : ""}`} role="switch" aria-checked={f.critical}
            onClick={() => change({ critical: !f.critical })}>
            <span className="audit-knob" aria-hidden /> Critical only
            {critical > 0 && <span className="badge critical" aria-label={`${critical} critical`}>{critical}</span>}
          </button>
          {phone ? (
            <FilterSheetButton active={chips.length}>
              {(close) => <FilterSheetBody {...filterProps} exporting={exporting} onExport={() => void doExport()} onDone={close} />}
            </FilterSheetButton>
          ) : <ExportButton busy={exporting} onClick={() => void doExport()} />}
        </div>
      </div>

      {sev && (
        <p className="audit-summary">
          <button className="audit-count crit" onClick={() => change({ severity: ["critical"], critical: false })}><strong>{critical} critical</strong></button>
          {" · "}<button className="audit-count" onClick={() => change({ severity: ["warning"], critical: false })}>{plural(sev.warning, "warning")}</button>
          {" · "}<button className="audit-count" onClick={() => change({ severity: [], critical: false })}>{plural(facets.data!.total, "action")}</button>
          {" "}{rangePhrase(f)}
        </p>
      )}

      {!phone && <FilterBar {...filterProps} />}

      {chips.length > 0 && (
        <div className="audit-chips row wrap" aria-label="Active filters">
          {chips.map((c) => (
            <span key={c.key} className="audit-chip">{c.label}
              <button aria-label={`Remove ${c.label}`} onClick={() => replace(c.without)}>×</button></span>
          ))}
          <button className="btn small ghost" onClick={() => replace(EMPTY)}>Clear all</button>
        </div>
      )}

      <Entries f={f} entries={entries} rows={rows} labels={labels} onFilter={change} onClear={() => replace(EMPTY)} />
    </div>
  );
}

/** Search "name, order #, dish…", written to the URL 300 ms after the last key. */
function SearchBox({ value, onCommit }: { value: string; onCommit: (q: string) => void }) {
  const [text, setText] = useState(value);
  const [seen, setSeen] = useState(value);
  if (value !== seen) {
    // The URL changed from outside (a chip ×, Clear all, back): show it, unless it's what was just typed.
    setSeen(value);
    if (value !== text.trim()) setText(value);
  }
  useEffect(() => {
    if (text.trim() === value) return;
    const id = window.setTimeout(() => onCommit(text.trim()), 300);
    return () => window.clearTimeout(id);
  }, [text, value, onCommit]);
  return (
    <label className="field grow audit-search"><span className="sr-only">Search the audit log</span>
      <Search size={16} aria-hidden className="audit-search-icon" />
      <input type="search" placeholder="Search name, order #, dish…" maxLength={60} value={text} onChange={(e) => setText(e.target.value)} /></label>
  );
}

type EntriesQuery = ReturnType<typeof useAuditEntries>;

function Entries({ f, entries, rows, labels, onFilter, onClear }: {
  f: AuditFilterState;
  entries: EntriesQuery;
  rows: AuditEntry[];
  labels: { categoryOf(c: string): string; roleOf(r: string): string };
  onFilter: (patch: Partial<AuditFilterState>) => void;
  onClear: () => void;
}) {
  if (entries.isPending) return <Empty>Loading the audit log…</Empty>;
  if (entries.isError && !rows.length) {
    const e = entries.error;
    if (e instanceof ApiError && e.status === 403) return <Empty>Only the owner can see the audit log.</Empty>;
    if (e instanceof ApiError && e.status === 400) return <div className="error-text" role="alert">{e.message}</div>;
    return <Empty>Couldn't load the audit log. <button className="btn small" onClick={() => void entries.refetch()}>Try again</button></Empty>;
  }
  if (!rows.length) {
    const onlyCritical = (f.critical || (f.severity.length === 1 && f.severity[0] === "critical"))
      && !isFiltered({ ...f, critical: false, severity: [] });
    if (onlyCritical) return <Empty><CircleCheck size={18} aria-hidden /> No critical actions in this period.</Empty>;
    if (isFiltered(f)) return <Empty>No actions match these filters. <button className="btn small" onClick={onClear}>Clear filters</button></Empty>;
    const days = rangeDays(f);
    return <Empty>{days ? `Nothing recorded between ${days}.` : "Nothing recorded yet."} Staff actions appear here as they happen.</Empty>;
  }
  return (
    <>
      {entries.isRefetchError && (
        <div className="muted small" role="status">Couldn't refresh. Showing the last loaded entries.{" "}
          <button className="btn small" onClick={() => void entries.refetch()}>Try again</button></div>
      )}
      {byDay(rows).map((g) => (
        <section key={g.day} className="audit-day" aria-label={g.day}>
          <h2 className="audit-day-head">{g.day}</h2>
          <ul className="audit-list">
            {g.entries.map((e) => (
              <AuditRow key={e.id} e={e} categoryLabel={labels.categoryOf(e.category)} roleLabel={labels.roleOf(e.actor.role)} onFilter={onFilter} />
            ))}
          </ul>
        </section>
      ))}
      <div className="audit-more-row">
        {entries.isFetchNextPageError ? (
          <div className="error-text" role="alert">Couldn't load more.{" "}
            <button className="btn small" onClick={() => void entries.fetchNextPage()}>Try again</button></div>
        ) : entries.hasNextPage ? (
          <button className="btn small" disabled={entries.isFetchingNextPage} onClick={() => void entries.fetchNextPage()}>
            {entries.isFetchingNextPage ? "Loading…" : "Show older"}</button>
        ) : <p className="muted small">That's everything for these filters.</p>}
      </div>
    </>
  );
}
