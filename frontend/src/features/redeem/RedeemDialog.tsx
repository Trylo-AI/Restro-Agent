// Redeem a code at the counter against the amount paid (docs/console.md#redeem-offer). The discount and the amount
// to collect come from the API (POST /offers/redeem); the console only parses the amount typed. One Idempotency-Key
// per dialog, reused on every retry of the same amount (an ambiguous send is checked, never blindly resent); a
// different amount after a failed attempt gets a new key (the server refuses a reused key with another amount).
import { useRef, useState } from "react";

import { useToasts } from "../../app/toasts";
import { Dialog } from "../../components/ui";
import { ApiError } from "../../lib/api/client";
import { inr } from "../../lib/format";
import { cafeClock, newIdempotencyKey, redeemAtCounter, type CounterRedemption, type OfferCheck, type OfferCustomer } from "../../lib/offers";
import { toPaise } from "../menu/MenuEditor";

export const MIN_BILL_PAISE = 100; // ₹ 1
export const MAX_BILL_PAISE = 10_000_000; // ₹ 1,00,000

/** The amount typed, in paise, or why it can't be used. `toPaise` rejects 0 and more than 2 decimals. */
export function parseAmount(text: string): { paise: number | null; error: string | null } {
  if (!text.trim()) return { paise: null, error: null };
  const paise = toPaise(text);
  if (paise === null) return { paise: null, error: "Enter the amount in rupees, like 1240 or 1240.50." };
  if (paise > MAX_BILL_PAISE) return { paise: null, error: "Check the amount: over ₹ 1,00,000" };
  if (paise < MIN_BILL_PAISE) return { paise: null, error: "Check the amount: at least ₹ 1" };
  return { paise, error: null };
}

export function RedeemDialog({ customer, offer, tz, offline, onClose, onRefetch, onRedeemed }: {
  customer: OfferCustomer; offer: OfferCheck; tz: string; offline: boolean;
  onClose: () => void;
  /** 409 / 422: the lookup is stale; refetch it. */
  onRefetch: () => void;
  onRedeemed: (r: CounterRedemption) => void;
}) {
  const toasts = useToasts();
  const [amount, setAmount] = useState("");
  const key = useRef(newIdempotencyKey()); // created when the dialog opens
  const sentPaise = useRef<number | null>(null); // the amount last sent with `key`
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ title?: string; text: string } | null>(null);
  const [done, setDone] = useState<CounterRedemption | null>(null);

  const { paise, error: amountError } = parseAmount(amount);
  const first = customer.first_name ?? (customer.deleted ? "Deleted customer" : "Customer");
  const percent = offer.discount_percent;
  const subtitle = [first, offer.campaign_name, percent ? `${percent} % off the bill` : `Offer: ${offer.offer_text ?? "no text"} · no bill discount`]
    .join(" · ");

  async function submit() {
    if (paise === null || busy || offline) return;
    setBusy(true);
    setError(null);
    // the same amount retypes to the same key; only another amount needs a new one
    if (sentPaise.current !== null && sentPaise.current !== paise) key.current = newIdempotencyKey();
    sentPaise.current = paise;
    try {
      const r = await redeemAtCounter(key.current, offer.code, paise);
      setDone(r);
      toasts.show(`${r.code} redeemed · ${inr(r.discount_paise)} off`);
      onRedeemed(r);
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, "network", "Can't reach Restro. Check the connection and try again.");
      if (err.status === 403) setError({ text: "You don't have access to this." });
      else if (err.code === "code_already_used") setError({ title: "Already used", text: err.message });
      else if (err.code === "already_redeemed_this_visit") setError({ title: "One offer per visit", text: err.message });
      else if (err.status === 0) setError({ text: `${err.message} Your amount is kept; Redeem again to retry.` });
      else setError({ text: err.message });
      if (err.status === 409 || err.status === 422) onRefetch();
    }
    setBusy(false);
  }

  if (done) {
    return (
      <Dialog title={`Redeem ${done.code}`} onClose={onClose}
        footer={<button className="btn primary" autoFocus onClick={onClose}>Done</button>}>
        <div className="redeem-done" role="status">
          <div className="collect">Collect {inr(done.total_paise)}</div>
          <p className="muted">
            Bill {inr(done.bill_paise)} · Discount −{inr(done.discount_paise)} · Redeemed by you at {cafeClock(done.redeemed_at, tz)}
          </p>
        </div>
      </Dialog>
    );
  }

  const label = paise === null ? "Redeem" : percent ? `Redeem ${percent} % off ${inr(paise)}` : `Redeem on ${inr(paise)}`;
  return (
    <Dialog title={`Redeem ${offer.code}`} onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={paise === null || busy || offline} onClick={() => void submit()}>
          {busy ? "Redeeming…" : label}
        </button>
      </>}>
      <p className="muted">{subtitle}</p>
      <form className="fields" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <label className="field">Amount paid (₹, before discount)
          <input inputMode="decimal" autoFocus value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="1240"
            disabled={busy} aria-invalid={!!amountError} />
        </label>
        {amountError && <p className="error-text">{amountError}</p>}
      </form>
      {offline && <p className="hint">You're offline. Redeem works again when the connection is back.</p>}
      {error && (
        <p className="error-text" role="alert">{error.title && <strong>{error.title}. </strong>}{error.text}</p>
      )}
    </Dialog>
  );
}
