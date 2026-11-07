// Live orders: card, dialogs and action feedback (docs/console.md#live-orders, "Done when").
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { ToastProvider } from "../../app/toasts";
import { ApiError } from "../../lib/api/client";
import type { LiveOrder } from "../../lib/data";
import { orders } from "../../lib/mutations";
import { orderUrgency } from "../../lib/urgency";
import { CancelDialog, DelayDialog } from "./dialogs";
import { OrderCard, type CardActions } from "./OrderCard";
import { canCancel, useOrderActions } from "./useOrderActions";

const session = vi.hoisted(() => ({ role: "shift_manager" as string }));
vi.mock("../../lib/session", async (orig) => ({
  ...(await orig<typeof import("../../lib/session")>()),
  useSession: () => ({ cafeId: "cafe-1", role: session.role, userId: "u-1", cafeName: "Kaapi House" }),
}));
vi.mock("../../lib/mutations", async (orig) => ({
  ...(await orig<typeof import("../../lib/mutations")>()),
  orders: { transition: vi.fn(), undo: vi.fn(), delay: vi.fn(), cancel: vi.fn(), manual: vi.fn() },
}));

const NOW = Date.parse("2026-10-24T12:00:00Z");

function liveOrder(over: Partial<LiveOrder> = {}): LiveOrder {
  return {
    id: "o-1", cafe_id: "cafe-1", customer_id: "cu-1", table_session_id: "ts-1", number: 1048, status: "NEW", flags: [],
    allergy_note: null, expected_minutes: 10, delay_minutes: 0, total_paise: 124000, source: "qr", version: 3,
    cancel_reason_code: null, created_at: new Date(NOW - 20_000).toISOString(), accepted_at: null, ready_at: null,
    served_at: null, cancelled_at: null, updated_at: new Date(NOW).toISOString(),
    items: [{ id: "i1", order_id: "o-1", item_id: "m1", name_snapshot: "Filter Coffee", price_snapshot_paise: 6000, qty: 2, note: "less sugar" }],
    table: "T5", customerName: "Riya", visits: 6, profileAllergies: [], escalations: [], ...over,
  };
}

const noop: CardActions = { advance: vi.fn(), delay: vi.fn(), cancel: vi.fn(), message: vi.fn(), print: vi.fn(), profile: vi.fn() };

function card(o: LiveOrder, opts: Partial<{ canCancel: boolean; offline: boolean; act: CardActions }> = {}) {
  return render(<OrderCard o={o} urgency={orderUrgency(o, NOW)} now={NOW} canCancel={opts.canCancel ?? true}
    offline={opts.offline ?? false} busy={false} act={opts.act ?? noop} />);
}

describe("order card", () => {
  test("shows table, number, visit, items with notes and the total from paise", () => {
    card(liveOrder());
    expect(screen.getByText("T5")).toBeInTheDocument();
    expect(screen.getByText(/Riya · 6th visit/)).toBeInTheDocument();
    expect(screen.getByText("less sugar")).toBeInTheDocument();
    expect(screen.getByText("₹ 1,240")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
  });

  test("allergy banner shows this visit's note and the profile allergy, until READY", () => {
    const { unmount } = card(liveOrder({ allergy_note: "no peanuts please", profileAllergies: ["sesame"] }));
    expect(screen.getByText("no peanuts please")).toBeInTheDocument();
    expect(screen.getByText("sesame")).toBeInTheDocument();
    unmount();
    card(liveOrder({ status: "READY", ready_at: new Date(NOW).toISOString(), allergy_note: "no peanuts please" }));
    expect(screen.queryByText("no peanuts please")).not.toBeInTheDocument();
  });

  test("Profile opens in one tap; Cancel hidden when not allowed; writes disabled offline", () => {
    const act = { ...noop, profile: vi.fn() };
    card(liveOrder(), { canCancel: false, offline: true, act });
    fireEvent.click(screen.getByRole("button", { name: /Profile of Riya/ }));
    expect(act.profile).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
  });

  test("first visit and manual orders read naturally", () => {
    const { unmount } = card(liveOrder({ visits: 1 }));
    expect(screen.getByText(/Riya · First visit/)).toBeInTheDocument();
    unmount();
    card(liveOrder({ customer_id: null, source: "manual", customerName: null }));
    expect(screen.getByText(/Manual order/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Profile/ })).not.toBeInTheDocument();
  });

  test("History links to the order's audit history, for the owner only", () => {
    for (const role of ["shift_manager", "manager"]) {
      session.role = role;
      const { unmount } = card(liveOrder());
      expect(screen.queryByRole("link", { name: /History/ })).not.toBeInTheDocument();
      unmount();
    }
    session.role = "owner";
    render(<MemoryRouter><OrderCard o={liveOrder()} urgency={orderUrgency(liveOrder(), NOW)} now={NOW} canCancel
      offline busy={false} act={noop} /></MemoryRouter>);
    expect(screen.getByRole("link", { name: /History/ })).toHaveAttribute("href", "/audit/orders/o-1"); // a read: offline too
    session.role = "shift_manager";
  });

  test("accepted card shows elapsed / budget including the delay", () => {
    card(liveOrder({ status: "ACCEPTED", accepted_at: new Date(NOW - 6 * 60_000).toISOString(), delay_minutes: 5 }));
    expect(screen.getByText("6 / 15 min")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark ready" })).toBeInTheDocument();
  });
});

test("shift_manager cancels only New; manager+ everywhere", () => {
  expect(canCancel("NEW", "shift_manager")).toBe(true);
  expect(canCancel("ACCEPTED", "shift_manager")).toBe(false);
  expect(canCancel("READY", "shift_manager")).toBe(false);
  expect(canCancel("ACCEPTED", "manager")).toBe(true);
  expect(canCancel("READY", "owner")).toBe(true);
});

describe("dialogs", () => {
  test("Cancel needs a reason, and text for Other", async () => {
    const submit = vi.fn().mockResolvedValue(true);
    render(<CancelDialog order={liveOrder()} onClose={() => undefined} onSubmit={submit} />);
    const go = screen.getByRole("button", { name: "Cancel order" });
    expect(go).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Other"));
    expect(go).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/What happened/), { target: { value: "  Gas ran out  " } });
    expect(go).toBeEnabled();
    await act(async () => fireEvent.click(go));
    expect(submit).toHaveBeenCalledWith("other", "Gas ran out");
    expect(screen.getByText("The customer gets an apology. Your own text stays with the team.")).toBeInTheDocument();
  });

  test("Delay defaults to +5 and previews the customer message; custom must be 1–99", () => {
    render(<DelayDialog order={liveOrder()} onClose={() => undefined} onSubmit={vi.fn()} />);
    expect(screen.getByText("Sorry for the wait — your order needs about 5 more minutes.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText(/Minutes/), { target: { value: "100" } });
    expect(screen.getByRole("button", { name: "Delay and tell the customer" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Minutes/), { target: { value: "12" } });
    expect(screen.getByRole("button", { name: "Delay and tell the customer" })).toBeEnabled();
  });
});

describe("actions", () => {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider><MemoryRouter>{children}</MemoryRouter></ToastProvider>
    </QueryClientProvider>
  );

  test("Accept sends expected_version, shows the undo toast, Undo reverts and says nothing was sent", async () => {
    vi.mocked(orders.transition).mockResolvedValue({} as never);
    vi.mocked(orders.undo).mockResolvedValue({} as never);
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(() => result.current.advance(liveOrder()));
    expect(orders.transition).toHaveBeenCalledWith("o-1", "ACCEPTED", 3);
    expect(await screen.findByText("#1048 → Accepted · Message sends in 5 s")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Undo" })));
    expect(orders.undo).toHaveBeenCalledWith("o-1");
    expect(await screen.findByText("#1048 back to New · Nothing was sent to the customer")).toBeInTheDocument();
  });

  test("409 says who changed it; 422 says it was already updated", async () => {
    vi.mocked(orders.transition).mockRejectedValueOnce(new ApiError(409, "version_conflict", "conflict"));
    const { result } = renderHook(() => useOrderActions(), { wrapper });
    await act(() => result.current.advance(liveOrder()));
    expect(await screen.findByText("Riya changed this order — check it again")).toBeInTheDocument();
    vi.mocked(orders.transition).mockRejectedValueOnce(new ApiError(422, "invalid_transition", "nope"));
    await act(() => result.current.advance(liveOrder()));
    expect(await screen.findByText("This order was already updated")).toBeInTheDocument();
  });
});
