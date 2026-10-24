// Urgency engine (docs/console.md#urgency-and-alerts). Pure functions; recomputed every second; never stored.
import type { Order, Request } from "./types";

export type Severity = "critical" | "warning" | "info" | "calm" | "none";
export type Sound = "alarm" | "two_note" | "chime";

export interface Urgency {
  severity: Severity;
  chip: string;
  sound: Sound | null; // played when an item first reaches this severity
  repeats: boolean; // critical alarms repeat every 30 s while open
}

const RANK: Record<Severity, number> = { critical: 4, warning: 3, info: 2, calm: 1, none: 0 };
export const rank = (s: Severity): number => RANK[s];

const sec = (iso: string | null | undefined, now: number) => (iso ? (now - Date.parse(iso)) / 1000 : 0);

export function dueAt(o: Order): number | null {
  if (!o.accepted_at) return null;
  return Date.parse(o.accepted_at) + (o.expected_minutes + o.delay_minutes) * 60_000;
}

/** Orders, first match wins. */
export function orderUrgency(o: Order, now: number): Urgency {
  if (o.status === "READY") {
    return { severity: "none", chip: `Ready · ${Math.floor(sec(o.ready_at, now) / 60)} min`, sound: null, repeats: false };
  }
  if (o.status === "NEW") {
    if (o.allergy_note) return { severity: "critical", chip: "Allergy", sound: "alarm", repeats: true };
    return sec(o.created_at, now) < 60
      ? { severity: "info", chip: "New", sound: "chime", repeats: false }
      : { severity: "warning", chip: "Not started", sound: "two_note", repeats: false };
  }
  if (o.status === "ACCEPTED") {
    const due = dueAt(o);
    if (o.flags.includes("overdue") || (due !== null && now >= due)) {
      return { severity: "critical", chip: "Overdue", sound: "alarm", repeats: true };
    }
    if (due !== null && o.accepted_at) {
      const total = due - Date.parse(o.accepted_at);
      if (total > 0 && now - Date.parse(o.accepted_at) >= 0.8 * total) {
        return { severity: "warning", chip: "Due soon", sound: "two_note", repeats: false };
      }
    }
    if (o.delay_minutes > 0) return { severity: "info", chip: `Delayed +${o.delay_minutes}`, sound: null, repeats: false };
  }
  return { severity: "none", chip: "", sound: null, repeats: false };
}

/** Calls (help, allergy, bill, low rating). `ackName` is who acknowledged it. */
export function requestUrgency(r: Request, now: number, ackName?: string | null): Urgency {
  if (r.status === "acknowledged" && (r.kind === "help" || r.kind === "allergy")) {
    return { severity: "info", chip: `With ${ackName ?? "staff"}`, sound: null, repeats: false };
  }
  if (r.status !== "open") return { severity: "none", chip: "", sound: null, repeats: false };
  switch (r.kind) {
    case "allergy":
      return { severity: "critical", chip: "Allergy", sound: "alarm", repeats: true };
    case "help":
      return sec(r.created_at, now) < 120
        ? { severity: "warning", chip: "Needs help", sound: "two_note", repeats: false }
        : { severity: "critical", chip: "Waiting too long", sound: "alarm", repeats: true };
    case "bill":
      return { severity: "warning", chip: "Bill", sound: null, repeats: false }; // badge only, silent
    case "low_rating":
      return { severity: "critical", chip: "Low rating", sound: "alarm", repeats: false }; // rings once
  }
}

/** Chats: bot → none; human: last message from staff or agent → calm; from the customer < 60 s warning, then critical. */
export function chatUrgency(mode: "bot" | "human", lastFromCustomerAt: string | null, lastFromUsAt: string | null,
  now: number): Urgency {
  if (mode === "bot") return { severity: "none", chip: "", sound: null, repeats: false };
  const unanswered = lastFromCustomerAt && (!lastFromUsAt || Date.parse(lastFromCustomerAt) > Date.parse(lastFromUsAt));
  if (!unanswered) return { severity: "calm", chip: "With staff", sound: null, repeats: false };
  return sec(lastFromCustomerAt, now) < 60
    ? { severity: "warning", chip: "Waiting", sound: "two_note", repeats: false }
    : { severity: "critical", chip: "Waiting too long", sound: "alarm", repeats: true };
}

export interface Tracked {
  key: string;
  urgency: Urgency;
}

/**
 * Ringing (docs: "only the highest new event per tick rings; first load and reconnect record silently;
 * critical repeats every 30 s while any critical item is open"). Keeps state between ticks.
 */
export class Ringer {
  private seen = new Map<string, Severity>();
  private primed = false;
  private lastRepeat = 0;

  /** Returns the sound to play now, if any. */
  tick(items: Tracked[], now: number, silent = false): Sound | null {
    let best: Sound | null = null;
    let bestRank = -1;
    const order: Record<Sound, number> = { alarm: 3, two_note: 2, chime: 1 };
    for (const { key, urgency } of items) {
      const prev = this.seen.get(key);
      if (this.primed && !silent && prev !== urgency.severity && urgency.sound && (prev === undefined || rank(urgency.severity) > rank(prev))) {
        if (order[urgency.sound] > bestRank) {
          best = urgency.sound;
          bestRank = order[urgency.sound];
        }
      }
      this.seen.set(key, urgency.severity);
    }
    const live = new Set(items.map((i) => i.key));
    for (const key of [...this.seen.keys()]) if (!live.has(key)) this.seen.delete(key);
    if (!this.primed) {
      // First load records silently; open criticals start their 30 s repeat from now.
      this.primed = true;
      this.lastRepeat = now;
      return null;
    }
    if (best) {
      if (best === "alarm") this.lastRepeat = now;
      return best;
    }
    const anyRepeating = items.some((i) => i.urgency.severity === "critical" && i.urgency.repeats);
    if (anyRepeating && !silent && now - this.lastRepeat >= 30_000) {
      this.lastRepeat = now;
      return "alarm";
    }
    return null;
  }

  /** First load, reconnect, café switch: record what's there without ringing. */
  reset(): void {
    this.primed = false;
    this.seen.clear();
  }
}

export function worst(items: Urgency[]): Severity {
  return items.reduce<Severity>((acc, u) => (rank(u.severity) > rank(acc) ? u.severity : acc), "none");
}

/** Nav badge: warning + critical count; red if any critical; hidden at 0. */
export function badge(items: Urgency[]): { count: number; critical: boolean } {
  const hot = items.filter((u) => u.severity === "warning" || u.severity === "critical");
  return { count: hot.length, critical: hot.some((u) => u.severity === "critical") };
}
