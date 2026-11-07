// One customer in the Redeem results: who they are (masked number only), their open table bill, and each offer code
// with the API's verdict, reason and warning. Only eligible codes get buttons; the console never decides eligibility.
import { useState } from "react";

import { ApiError } from "../../lib/api/client";
import { inr, ordinal } from "../../lib/format";
import { bills } from "../../lib/mutations";
import { cafeClock, cafeDay, cafeDayMonth, channelLabel, type OfferCheck, type OfferCustomer } from "../../lib/offers";
import { verdictLook } from "./verdict";

function VerdictChip({ verdict }: { verdict: string }) {
  const v = verdictLook(verdict);
  const Icon = v.icon;
  return <span className={`chip ${v.cls}`}><Icon size={13} aria-hidden /> {v.label}</span>;
}

/** "Counter · 12:41 · by Ravi" (or "12 Oct" when it wasn't today), from the API's redemption. */
function usedLine(o: OfferCheck, tz: string): string | null {
  const r = o.redemption;
  if (!r) return null;
  const when = cafeDay(tz, new Date(r.redeemed_at)) === cafeDay(tz) ? cafeClock(r.redeemed_at, tz) : cafeDayMonth(r.redeemed_at, tz);
  return [channelLabel(r, true), when, r.redeemed_by_name ? `by ${r.redeemed_by_name}` : null, `${inr(r.discount_paise)} off`]
    .filter(Boolean).join(" · ");
}

export function OfferCard({ customer, tz, offline, onRedeem, onChanged }: {
  customer: OfferCustomer; tz: string; offline: boolean;
  onRedeem: (customer: OfferCustomer, offer: OfferCheck) => void;
  /** After Apply (or a refusal): refetch the lookup and Redeemed today. */
  onChanged: () => void;
}) {
  const c = customer;
  const bill = c.open_bill ?? null;
  const [applied, setApplied] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  async function apply(o: OfferCheck) {
    if (!bill) return;
    setBusy(o.recipient_id);
    setErrors((e) => ({ ...e, [o.recipient_id]: "" }));
    try {
      const res = await bills.applyCode(bill.table_session_id, o.code);
      setApplied((a) => ({ ...a, [o.recipient_id]: `Applied to ${bill.table_label} bill · ${inr(res.discount_paise ?? 0)} off` }));
      onChanged();
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      setErrors((x) => ({ ...x, [o.recipient_id]: err?.status === 403 ? "You don't have access to this." : err?.message ?? "Couldn't apply. Try again." }));
      if (err && (err.status === 409 || err.status === 422)) onChanged();
    }
    setBusy(null);
  }

  const first = c.first_name ?? c.name ?? null;
  return (
    <article className="offer-card" aria-label={c.deleted ? "Deleted customer" : first ?? "Customer"}>
      <div className="offer-who">
        {c.deleted ? <strong>Deleted customer</strong> : (
          <>
            <strong>{first ?? "No name"}</strong>
            {c.name && c.name !== first && <span className="muted"> {c.name}</span>}
          </>
        )}
        {c.masked_phone && <span className="muted mono small">{c.masked_phone}</span>}
        <span className="muted small">
          {c.visits ? `${ordinal(c.visits)} visit` : "No visits yet"}{c.last_visit_at ? ` · last ${cafeDayMonth(c.last_visit_at, tz)}` : ""}
        </span>
      </div>
      {bill && (
        <div className="chips row">
          {bill.code
            ? <span className="chip calm">{bill.table_label} bill already has code <span className="mono">{bill.code}</span></span>
            : <span className="chip info">At {bill.table_label} · bill open · {inr(bill.subtotal_paise)}</span>}
        </div>
      )}
      {c.offers.length === 0 ? <p className="muted">No offers for {first ?? "this customer"}.</p> : (
        <ul className="offer-rows">
          {c.offers.map((o) => {
            const used = usedLine(o, tz);
            const eligible = o.verdict === "eligible";
            return (
              <li key={o.recipient_id} className={`offer-row${o.matched ? " matched" : ""}`}>
                <div className="offer-line">
                  <span className={`mono offer-code${o.matched ? " hit" : ""}`}>{o.code}</span>
                  <VerdictChip verdict={o.verdict} />
                </div>
                <div className="small">
                  <strong>{o.campaign_name}</strong>
                  {o.offer_text && <span> · {o.offer_text}</span>}
                  {o.valid_until && <span className="muted"> · valid till {cafeDayMonth(o.valid_until, tz)}</span>}
                </div>
                <p className="offer-reason small">{o.reason}</p>
                {used && o.verdict === "redeemed" && <p className="muted small">{used}</p>}
                {o.warning && <p className="offer-warning small" role="note">{o.warning}</p>}
                {applied[o.recipient_id] && <p className="offer-done small" role="status">{applied[o.recipient_id]}</p>}
                {errors[o.recipient_id] && <p className="error-text" role="alert">{errors[o.recipient_id]}</p>}
                {eligible && !applied[o.recipient_id] && (
                  <div className="row offer-actions">
                    <button className="btn primary small" disabled={offline || busy !== null} onClick={() => onRedeem(c, o)}>Redeem at counter</button>
                    {bill && !bill.code && (
                      <button className="btn small" disabled={offline || busy !== null} onClick={() => void apply(o)}>
                        {busy === o.recipient_id ? "Applying…" : `Apply to ${bill.table_label} bill`}
                      </button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}
