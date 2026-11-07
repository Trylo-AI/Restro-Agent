// Verdict chips for offer codes: colour + icon + label, never colour alone. The verdict and its reason come from the
// API (domain/campaigns/redemption.py); this only maps them to a look.
import { Ban, CircleCheck, Clock, Hourglass, TicketCheck, TriangleAlert, type LucideIcon } from "lucide-react";

import type { OfferVerdict } from "../../lib/offers";

export const VERDICT: Record<OfferVerdict, { label: string; cls: "ok" | "calm" | "warning"; icon: LucideIcon }> = {
  eligible: { label: "Eligible", cls: "ok", icon: CircleCheck },
  redeemed: { label: "Used", cls: "calm", icon: TicketCheck },
  expired: { label: "Expired", cls: "calm", icon: Clock },
  campaign_cancelled: { label: "Cancelled", cls: "calm", icon: Ban },
  not_sent: { label: "Not sent yet", cls: "warning", icon: Hourglass },
  not_delivered: { label: "Didn't reach them", cls: "warning", icon: TriangleAlert },
};

/** An unknown verdict (a newer backend) still renders, calmly, with its API reason. */
export function verdictLook(v: string) {
  return VERDICT[v as OfferVerdict] ?? { label: v, cls: "calm" as const, icon: Clock };
}
