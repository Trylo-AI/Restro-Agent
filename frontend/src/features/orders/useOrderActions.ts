// Order writes with the screen's feedback: undo toast, 409 / 422 messages, refetch (docs/console.md#live-orders).
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { useToasts } from "../../app/toasts";
import { ApiError } from "../../lib/api/client";
import type { LiveOrder } from "../../lib/data";
import { conversations, orders, type CancelReason } from "../../lib/mutations";
import { atLeast, useSession } from "../../lib/session";
import type { OrderStatus, Role } from "../../lib/types";

const LABEL: Record<OrderStatus, string> = {
  NEW: "New", ACCEPTED: "Accepted", READY: "Ready", SERVED: "Served", CANCELLED: "Cancelled",
};

export const NEXT: Partial<Record<OrderStatus, { to: "ACCEPTED" | "READY" | "SERVED"; label: string }>> = {
  NEW: { to: "ACCEPTED", label: "Accept" },
  ACCEPTED: { to: "READY", label: "Mark ready" },
  READY: { to: "SERVED", label: "Mark served" },
};

/** shift_manager cancels only New orders; manager+ in every column (the API enforces the same). */
export function canCancel(status: OrderStatus, role: Role | null): boolean {
  return status === "NEW" ? atLeast(role, "shift_manager") : atLeast(role, "manager");
}

export function useOrderActions() {
  const toasts = useToasts();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { cafeId } = useSession();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["orders", cafeId] });

  async function run<T>(o: LiveOrder, fn: () => Promise<T>): Promise<T | null> {
    try {
      const out = await fn();
      refresh();
      return out;
    } catch (e) {
      refresh();
      if (e instanceof ApiError && e.status === 409) {
        toasts.show(`${o.customerName ?? "Someone"} changed this order — check it again`, true);
      } else if (e instanceof ApiError && e.status === 422 && e.code === "invalid_transition") {
        toasts.show("This order was already updated", true);
      } else {
        toasts.show(e instanceof Error ? e.message : "Something went wrong. Please try again.", true);
      }
      return null;
    }
  }

  return {
    async advance(o: LiveOrder) {
      const next = NEXT[o.status];
      if (!next) return;
      const done = await run(o, () => orders.transition(o.id, next.to, o.version));
      if (!done) return;
      toasts.undo(`#${o.number} → ${LABEL[next.to]} · Message sends in 5 s`, async () => {
        try {
          await orders.undo(o.id);
          toasts.show(`#${o.number} back to ${LABEL[o.status]} · Nothing was sent to the customer`);
        } catch (e) {
          toasts.show(e instanceof Error ? e.message : "Couldn't undo", true);
        } finally {
          refresh();
        }
      });
    },
    async delay(o: LiveOrder, minutes: number, note: string | null) {
      const done = await run(o, () => orders.delay(o.id, minutes, note));
      if (done) toasts.show(`#${o.number} delayed +${minutes} min · Customer told`);
      return !!done;
    },
    async cancel(o: LiveOrder, reason: CancelReason, text: string | null) {
      toasts.clearUndo();
      const done = await run(o, () => orders.cancel(o.id, reason, text, o.version));
      if (done) toasts.show(`#${o.number} cancelled · The customer gets an apology`);
      return !!done;
    },
    async message(o: LiveOrder) {
      if (!o.table_session_id) {
        toasts.show("This order has no table chat", true);
        return;
      }
      const conv = await run(o, () => conversations.open(o.table_session_id!));
      if (conv) navigate(`/chats/${conv.id}`);
    },
  };
}
