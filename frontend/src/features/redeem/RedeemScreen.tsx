// Redeem an offer at the counter (docs/console.md#redeem-offer): `/redeem` for shift_manager+, and the same body as
// the Campaigns Redeem tab (`/campaigns/redeem`, owner). Search by name, the last 4 digits or the code; the verdicts,
// reasons and amounts come from the API. "Redeemed today" is the café-local day from v_redemptions.
import "../../styles/campaigns.css";

import { useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";

import { useOffline } from "../../app/App";
import { Empty } from "../../components/ui";
import { ApiError } from "../../lib/api/client";
import { inr } from "../../lib/format";
import {
  cafeClock, channelLabel, digitsHint, searchable, staffLabel, useCafeTimezone, useOfferLookup, useRedemptionsToday,
  DEFAULT_TZ, type OfferCheck, type OfferCustomer,
} from "../../lib/offers";
import { atLeast, useSession } from "../../lib/session";
import { OfferCard } from "./OfferCard";
import { RedeemDialog } from "./RedeemDialog";

export const DEBOUNCE_MS = 300;

export function RedeemScreen() {
  const { role } = useSession();
  if (!atLeast(role, "shift_manager")) return <Navigate to="/orders" replace />;
  return <RedeemBody />;
}

/** The Redeem tab inside Campaigns (`/campaigns/redeem`, owner): the same body as `/redeem`. */
export function RedeemPanel() {
  const { role } = useSession();
  if (!atLeast(role, "shift_manager")) return <Navigate to="/orders" replace />;
  return <RedeemBody />;
}

function RedeemBody() {
  const { cafeId } = useSession();
  const qc = useQueryClient();
  const offline = useOffline();
  const tz = useCafeTimezone().data ?? DEFAULT_TZ;
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<{ customer: OfferCustomer; offer: OfferCheck } | null>(null);

  useEffect(() => {
    const id = window.setTimeout(() => setQ(text.trim()), DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [text]);

  const lookup = useOfferLookup(q);
  const hint = digitsHint(text);
  const active = searchable(q) && !digitsHint(text);

  function refresh() {
    void lookup.refetch();
    void qc.invalidateQueries({ queryKey: ["redemptions-today", cafeId] });
  }

  return (
    <div className="redeem">
      <div>
        <h2>Redeem an offer</h2>
        <p className="hint">Search by name, the last 4 digits of their number, or the code they show you.</p>
      </div>
      <label className="redeem-search">
        <Search size={18} aria-hidden />
        <input type="search" autoFocus enterKeyHint="search" value={text} maxLength={40} aria-label="Search offers"
          placeholder="Riya, 4421 or BACK20-7KQ" onChange={(e) => setText(e.target.value)} />
      </label>
      {hint && <p className="hint" role="status">{hint}</p>}
      {offline && <p className="hint">You're offline. Redeem and Apply work again when the connection is back.</p>}
      {active && <Results q={q} lookup={lookup} tz={tz} offline={offline} onRedeem={(customer, offer) => setOpen({ customer, offer })}
        onChanged={refresh} />}
      <RedeemedToday tz={tz} />
      {open && (
        <RedeemDialog customer={open.customer} offer={open.offer} tz={tz} offline={offline} onClose={() => setOpen(null)}
          onRefetch={() => void lookup.refetch()} onRedeemed={refresh} />
      )}
    </div>
  );
}

function Results({ q, lookup, tz, offline, onRedeem, onChanged }: {
  q: string; lookup: ReturnType<typeof useOfferLookup>; tz: string; offline: boolean;
  onRedeem: (customer: OfferCustomer, offer: OfferCheck) => void; onChanged: () => void;
}) {
  if (lookup.isError) {
    const err = lookup.error instanceof ApiError ? lookup.error : null;
    const text = err?.status === 400 ? err.message : err?.status === 403 ? "You don't have access to this." : "Couldn't search. Try again.";
    return (
      <div className="empty" role="alert">
        {text} {err?.status !== 400 && <button className="btn small" onClick={() => void lookup.refetch()}>Retry</button>}
      </div>
    );
  }
  if (!lookup.data) return <Empty>Searching…</Empty>;
  const { customers, truncated } = lookup.data;
  if (!customers.length) {
    return <Empty>No customer matches “{q}”. Try the last 4 digits of their number or the code.</Empty>;
  }
  return (
    <section className="offer-results" aria-label="Results">
      {customers.map((c) => <OfferCard key={c.customer_id} customer={c} tz={tz} offline={offline} onRedeem={onRedeem} onChanged={onChanged} />)}
      {truncated && <p className="hint">Showing 20. Type more of the name.</p>}
    </section>
  );
}

function RedeemedToday({ tz }: { tz: string }) {
  const q = useRedemptionsToday();
  return (
    <section className="redeemed-today" aria-label="Redeemed today">
      <h3>Redeemed today</h3>
      {q.isError ? <p className="error-text">Couldn't load today's redemptions. They refresh every 30 s.</p>
        : !q.data ? <p className="muted">Loading…</p>
          : !q.data.length ? <p className="muted">Nothing redeemed yet today.</p> : (
            <ul className="plist">
              {q.data.map((r) => (
                <li key={r.redemption_id} className="redeemed-row">
                  <span className="mono">{cafeClock(r.redeemed_at, tz)}</span>
                  <span>{r.first_name ?? "Customer"}{r.masked_phone && <span className="muted mono small"> {r.masked_phone}</span>}</span>
                  <span className="mono">{r.code}</span>
                  <span>{channelLabel(r)}</span>
                  <span>−{inr(r.discount_paise)}</span>
                  <span className="muted">by {staffLabel(r)}</span>
                </li>
              ))}
            </ul>
          )}
    </section>
  );
}
