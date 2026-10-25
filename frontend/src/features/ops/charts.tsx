// Hand-drawn SVG charts for the performance pages. One y-axis per chart, 2px lines, thin bars with a 2px surface
// gap, recessive grid, a legend for ≥ 2 series, and a hover tooltip on every chart. Outcome colours are the
// validated status set (good / warning / critical); identity is never colour alone (legend, labels, tables).
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export const COLOR = {
  p50: "#2a78d6", // categorical slot 1
  p95: "#eb6834", // categorical slot 2
  ok: "#2f9e5b", // status good
  degraded: "#eda100", // status warning (below 3:1: always labelled)
  failed: "#d03b3b", // status critical
  bar: "#2a78d6",
};

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

export function fmtN(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : Math.round(n).toLocaleString("en-IN");
}

export function fmtPct(r: number | null | undefined, digits = 1): string {
  return r === null || r === undefined ? "—" : `${(r * 100).toFixed(digits)} %`;
}

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.floor(e!.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

const timeLabel = (t: number, spanS: number) =>
  new Date(t).toLocaleString("en-IN", spanS > 2 * 86400 ? { day: "numeric", month: "short" } : { hour: "2-digit", minute: "2-digit" });

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="viz-legend" role="list">
      {items.map((i) => (
        <span key={i.label} role="listitem"><i style={{ background: i.dashed ? "transparent" : i.color, borderColor: i.color }} className={i.dashed ? "dashed" : ""} />{i.label}</span>
      ))}
    </div>
  );
}

function Tooltip({ x, y, width, children }: { x: number; y: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(x + 12, 0), width - 180);
  return <div className="viz-tip" style={{ left, top: Math.max(y - 10, 0) }}>{children}</div>;
}

export interface Line { key: string; label: string; color: string; points: { t: number; v: number | null }[] }

/** Lines over time on one ms axis, with threshold lines (the latency budget) and deploy markers. */
export function TimeChart({ lines, thresholds = [], markers = [], spanS, height = 200, unit = "ms" }: {
  lines: Line[]; thresholds?: { v: number; label: string }[]; markers?: { t: number; label: string }[]; spanS: number;
  height?: number; unit?: "ms" | "n";
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 52, r: 12, t: 10, b: 24 };
  const ts = useMemo(() => [...new Set(lines.flatMap((l) => l.points.map((p) => p.t)))].sort((a, b) => a - b), [lines]);
  const maxV = niceMax(Math.max(1, ...lines.flatMap((l) => l.points.map((p) => p.v ?? 0)), ...thresholds.map((t) => t.v * 1.05)));
  if (!ts.length) return <div className="viz-empty">No data in this range.</div>;
  const t0 = ts[0]!, t1 = ts.length > 1 ? ts[ts.length - 1]! : ts[0]! + 1;
  const x = (t: number) => pad.l + ((t - t0) / (t1 - t0 || 1)) * (width - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / maxV) * (height - pad.t - pad.b);
  const fmt = unit === "ms" ? fmtMs : fmtN;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxV);
  const xt = ts.filter((_, i) => i % Math.max(1, Math.ceil(ts.length / 6)) === 0);
  const ht = hover !== null ? ts[hover] : null;

  return (
    <div className="viz" ref={ref}>
      {(lines.length > 1 || thresholds.length > 0) && <Legend items={[...lines.map((l) => ({ label: l.label, color: l.color })), ...thresholds.map((t) => ({ label: t.label, color: "#8a8584", dashed: true }))]} />}
      <svg width={width} height={height} role="img" aria-label={lines.map((l) => l.label).join(", ")}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const mx = e.clientX - r.left;
          let best = 0;
          ts.forEach((t, i) => { if (Math.abs(x(t) - mx) < Math.abs(x(ts[best]!) - mx)) best = i; });
          setHover(best);
        }}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={width - pad.r} y1={y(v)} y2={y(v)} className="viz-grid" />
            <text x={pad.l - 6} y={y(v) + 4} textAnchor="end" className="viz-axis">{fmt(v)}</text>
          </g>
        ))}
        {xt.map((t) => <text key={t} x={x(t)} y={height - 6} textAnchor="middle" className="viz-axis">{timeLabel(t, spanS)}</text>)}
        {thresholds.map((th) => (
          <g key={th.label}>
            <line x1={pad.l} x2={width - pad.r} y1={y(th.v)} y2={y(th.v)} className="viz-threshold"><title>{th.label}</title></line>
          </g>
        ))}
        {markers.filter((m) => m.t >= t0 && m.t <= t1).map((m) => (
          <g key={m.t}><line x1={x(m.t)} x2={x(m.t)} y1={pad.t} y2={height - pad.b} className="viz-marker" /><title>{m.label}</title></g>
        ))}
        {lines.map((l) => {
          const d = l.points.filter((p) => p.v !== null).map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v!).toFixed(1)}`).join("");
          return <path key={l.key} d={d} fill="none" stroke={l.color} strokeWidth={2} strokeLinejoin="round" />;
        })}
        {lines.map((l) => l.points.length === 1 && l.points[0]!.v !== null
          ? <circle key={`${l.key}-dot`} cx={x(l.points[0]!.t)} cy={y(l.points[0]!.v!)} r={4} fill={l.color} /> : null)}
        {ht !== null && ht !== undefined && (
          <g>
            <line x1={x(ht)} x2={x(ht)} y1={pad.t} y2={height - pad.b} className="viz-crosshair" />
            {lines.map((l) => {
              const p = l.points.find((q) => q.t === ht);
              return p && p.v !== null ? <circle key={l.key} cx={x(ht)} cy={y(p.v)} r={4} fill={l.color} stroke="var(--color-card)" strokeWidth={2} /> : null;
            })}
          </g>
        )}
      </svg>
      {ht !== null && ht !== undefined && (
        <Tooltip x={x(ht)} y={pad.t} width={width}>
          <strong>{timeLabel(ht, spanS < 86400 ? 0 : spanS)} {new Date(ht).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</strong>
          {lines.map((l) => <div key={l.key}><i style={{ background: l.color }} />{l.label}: {fmt(l.points.find((q) => q.t === ht)?.v ?? null)}</div>)}
        </Tooltip>
      )}
    </div>
  );
}

export interface StackKey { key: string; label: string; color: string }

/** Counts per interval stacked by outcome; 2px surface gap between segments. */
export function StackedBars({ rows, keys, spanS, height = 180, format = fmtN, legend = true }: {
  rows: ({ t: number } & Record<string, number>)[]; keys: StackKey[]; spanS: number; height?: number;
  format?: (v: number) => string; legend?: boolean;
}) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 60, r: 8, t: 8, b: 24 };
  if (!rows.length) return <div className="viz-empty">No data in this range.</div>;
  const totals = rows.map((r) => keys.reduce((s, k) => s + (r[k.key] ?? 0), 0));
  const maxV = niceMax(Math.max(1, ...totals));
  const slot = (width - pad.l - pad.r) / rows.length;
  const bw = Math.max(2, Math.min(28, slot - 2));
  const y = (v: number) => pad.t + (1 - v / maxV) * (height - pad.t - pad.b);
  const xt = rows.filter((_, i) => i % Math.max(1, Math.ceil(rows.length / 6)) === 0);
  return (
    <div className="viz" ref={ref}>
      {legend && <Legend items={keys} />}
      <svg width={width} height={height} role="img" aria-label={`Counts by ${keys.map((k) => k.label).join(", ")}`} onMouseLeave={() => setHover(null)}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}><line x1={pad.l} x2={width - pad.r} y1={y(f * maxV)} y2={y(f * maxV)} className="viz-grid" />
            <text x={pad.l - 6} y={y(f * maxV) + 4} textAnchor="end" className="viz-axis">{format(f * maxV)}</text></g>
        ))}
        {rows.map((r, i) => {
          let acc = 0;
          const cx = pad.l + slot * i + (slot - bw) / 2;
          return (
            <g key={r.t} onMouseEnter={() => setHover(i)}>
              <rect x={pad.l + slot * i} y={pad.t} width={slot} height={height - pad.t - pad.b} fill="transparent" />
              {keys.map((k) => {
                const v = r[k.key] ?? 0;
                if (!v) return null;
                const top = y(acc + v), bottom = y(acc);
                acc += v;
                return <rect key={k.key} x={cx} y={top} width={bw} height={Math.max(1, bottom - top - 2)} fill={k.color} />;
              })}
            </g>
          );
        })}
        {xt.map((r) => {
          const i = rows.indexOf(r);
          return <text key={r.t} x={pad.l + slot * i + slot / 2} y={height - 6} textAnchor="middle" className="viz-axis">{timeLabel(r.t, spanS)}</text>;
        })}
      </svg>
      {hover !== null && rows[hover] && (
        <Tooltip x={pad.l + slot * hover + slot / 2} y={pad.t} width={width}>
          <strong>{timeLabel(rows[hover]!.t, spanS)}</strong>
          {keys.map((k) => <div key={k.key}><i style={{ background: k.color }} />{k.label}: {format(rows[hover]![k.key] ?? 0)}</div>)}
        </Tooltip>
      )}
    </div>
  );
}

/** Horizontal bars of a share (0–1), value labelled in text ink at the bar's end. */
export function ShareBars({ rows, onPick }: { rows: { label: string; share: number; detail?: string; key?: string }[]; onPick?: (key: string) => void }) {
  const max = Math.max(0.0001, ...rows.map((r) => r.share));
  if (!rows.length) return <div className="viz-empty">No data in this range.</div>;
  return (
    <ul className="share-bars">
      {rows.map((r) => (
        <li key={r.key ?? r.label} title={r.detail}>
          {onPick ? <button className="link" onClick={() => onPick(r.key ?? r.label)}>{r.label}</button> : <span>{r.label}</span>}
          <span className="track"><span className="fill" style={{ width: `${(r.share / max) * 100}%` }} /></span>
          <span className="val">{fmtPct(r.share)}{r.detail ? <small> · {r.detail}</small> : null}</span>
        </li>
      ))}
    </ul>
  );
}

/** Duration histogram on the fixed PDS buckets. */
export function Histogram({ buckets, height = 140 }: { buckets: { le: number | null; n: number }[]; height?: number }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const shown = buckets.slice(buckets.findIndex((b) => b.n > 0), buckets.length - [...buckets].reverse().findIndex((b) => b.n > 0));
  if (!shown.length) return <div className="viz-empty">No data in this range.</div>;
  const pad = { l: 40, r: 8, t: 8, b: 24 };
  const maxV = niceMax(Math.max(...shown.map((b) => b.n)));
  const slot = (width - pad.l - pad.r) / shown.length;
  const y = (v: number) => pad.t + (1 - v / maxV) * (height - pad.t - pad.b);
  const label = (b: { le: number | null }) => (b.le === null ? "more" : `≤ ${fmtMs(b.le)}`);
  return (
    <div className="viz" ref={ref}>
      <svg width={width} height={height} role="img" aria-label="Duration histogram" onMouseLeave={() => setHover(null)}>
        {[0, 1].map((f) => <g key={f}><line x1={pad.l} x2={width - pad.r} y1={y(f * maxV)} y2={y(f * maxV)} className="viz-grid" />
          <text x={pad.l - 6} y={y(f * maxV) + 4} textAnchor="end" className="viz-axis">{fmtN(f * maxV)}</text></g>)}
        {shown.map((b, i) => (
          <g key={i} onMouseEnter={() => setHover(i)}>
            <rect x={pad.l + slot * i} y={pad.t} width={slot} height={height - pad.t - pad.b} fill="transparent" />
            <rect x={pad.l + slot * i + 1} y={y(b.n)} width={Math.max(1, slot - 2)} height={Math.max(0, height - pad.b - y(b.n))} fill={COLOR.bar} />
            {(i % Math.max(1, Math.ceil(shown.length / 7)) === 0) && <text x={pad.l + slot * i + slot / 2} y={height - 6} textAnchor="middle" className="viz-axis">{label(b)}</text>}
          </g>
        ))}
      </svg>
      {hover !== null && <Tooltip x={pad.l + slot * hover} y={pad.t} width={width}><strong>{label(shown[hover]!)}</strong><div>{fmtN(shown[hover]!.n)} runs</div></Tooltip>}
    </div>
  );
}

/** A tiny trend line for tables; the title carries the values for hover and screen readers. */
export function Spark({ values, label }: { values: (number | null)[]; label: string }) {
  const vs = values.map((v) => v ?? 0);
  if (vs.length < 2) return <span className="muted small">—</span>;
  const max = Math.max(1, ...vs), w = 80, h = 22;
  const d = vs.map((v, i) => `${i ? "L" : "M"}${((i / (vs.length - 1)) * w).toFixed(1)},${(h - 2 - (v / max) * (h - 4)).toFixed(1)}`).join("");
  return (
    <svg width={w} height={h} className="spark" role="img" aria-label={label}>
      <title>{`${label}: ${vs.map((v) => fmtMs(v)).join(", ")}`}</title>
      <path d={d} fill="none" stroke={COLOR.p50} strokeWidth={1.5} />
    </svg>
  );
}
