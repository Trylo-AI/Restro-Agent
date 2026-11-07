// Labels shared by the campaign screens (list, detail, insights, wizard). Text only; no data.
import type { CampaignRow } from "../../lib/data";
import type { CampaignInput } from "../../lib/mutations";
import type { RecipientStatus } from "../../lib/campaignInsights";

export const SEGMENTS: [CampaignInput["segment"], string, string][] = [
  ["all_opted_in", "Everyone opted in", "Every customer who said yes to offers"],
  ["lapsed", "Lapsed", "No order in the last 30 days"],
  ["regulars", "Regulars", "3 or more orders in the last 60 days"],
];
export const SEGMENT_LABEL: Record<string, string> = Object.fromEntries(SEGMENTS.map(([k, l]) => [k, l]));
export const STATUS: Record<CampaignRow["status"], { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "calm" }, scheduled: { label: "Scheduled", cls: "info" }, sending: { label: "Sending", cls: "warning" },
  completed: { label: "Completed", cls: "ok" }, cancelled: { label: "Cancelled", cls: "calm" },
};
export const RECIPIENT_STATUS: Record<RecipientStatus, { label: string; cls: string }> = {
  queued: { label: "Not sent", cls: "calm" }, deferred: { label: "Not sent", cls: "calm" }, sent: { label: "Sent", cls: "info" },
  delivered: { label: "Delivered", cls: "info" }, read: { label: "Read", cls: "ok" }, held: { label: "Held", cls: "warning" },
  failed: { label: "Failed", cls: "critical" },
};
/** How a customer opted out (consents.source), as the owner reads it. */
export const OPT_OUT_SOURCE: Record<string, string> = {
  keyword: "Replied STOP", button: "Tapped Stop offers", meta_block: "Blocked on WhatsApp",
};
export const HELD_TIP = "Normal: WhatsApp limits how many marketing messages one person gets from all businesses.";

export function pct(n: number, of: number): string {
  return of ? `${Math.round((n / of) * 100)} %` : "—";
}
