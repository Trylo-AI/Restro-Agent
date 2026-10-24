// Formatters. Money is never computed here: values arrive in paise from the API or the DB.

/** 124000 paise → "₹ 1,240"; 12345 → "₹ 123.45" (Indian digit grouping). */
export function inr(paise: number): string {
  const rupees = Math.trunc(paise / 100);
  const rem = Math.abs(paise % 100);
  const s = String(Math.abs(rupees));
  let grouped = s;
  if (s.length > 3) {
    const head = s.slice(0, -3);
    const tail = s.slice(-3);
    grouped = head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
  }
  return `₹ ${rupees < 0 ? "-" : ""}${grouped}${rem ? "." + String(rem).padStart(2, "0") : ""}`;
}

/** "919876543421" → "+91 98••• ••421". Full numbers never reach the console UI. */
export function maskedPhone(waId: string | null | undefined): string {
  if (!waId || waId.length < 10) return "••••";
  return `+${waId.slice(0, 2)} ${waId.slice(2, 4)}••• ••${waId.slice(-3)}`;
}

export function minutesSince(iso: string | null | undefined, now: number): number {
  if (!iso) return 0;
  return Math.max(0, Math.floor((now - Date.parse(iso)) / 60000));
}

export function secondsSince(iso: string | null | undefined, now: number): number {
  if (!iso) return 0;
  return Math.max(0, Math.floor((now - Date.parse(iso)) / 1000));
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

/** "12 Oct" whatever the browser's locale. */
export function dayMonth(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function relative(iso: string | null | undefined, now: number): string {
  if (!iso) return "";
  const s = Math.floor((now - Date.parse(iso)) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

export function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]!);
}

/** "T5" → "T5"; staff see table labels as they are. */
export function tableLabel(label: string | null | undefined): string {
  return label ? label : "—";
}
