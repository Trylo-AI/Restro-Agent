// Chats, Calls and bills, Menu (docs/console.md, each screen's "Done when").
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ToastProvider } from "../app/toasts";
import * as data from "../lib/data";
import { messageText } from "../lib/data";
import { bills, conversations, menu, requests } from "../lib/mutations";
import type { MenuItem } from "../lib/types";
import { CallsScreen } from "./calls/CallsScreen";
import { ChatsScreen } from "./chats/ChatsScreen";
import { ARM_MS, MenuScreen } from "./menu/MenuScreen";

const session = vi.hoisted(() => ({ role: "shift_manager" as string }));
vi.mock("../lib/session", async (orig) => ({
  ...(await orig<typeof import("../lib/session")>()),
  useSession: () => ({ cafeId: "cafe-1", role: session.role, userId: "u-1", cafeName: "Kaapi House" }),
}));
vi.mock("../lib/data", async (orig) => ({
  ...(await orig<typeof import("../lib/data")>()),
  useMenu: vi.fn(), useOpenRequests: vi.fn(), useBill: vi.fn(), useChats: vi.fn(), useThread: vi.fn(),
  useOpenSessions: vi.fn(), useCustomerProfile: vi.fn(() => ({ isPending: true })),
}));
vi.mock("../lib/mutations", async (orig) => ({
  ...(await orig<typeof import("../lib/mutations")>()),
  menu: { setAvailable: vi.fn().mockResolvedValue({}) },
  requests: { ack: vi.fn().mockResolvedValue({}), resolve: vi.fn().mockResolvedValue({}) },
  bills: { deliver: vi.fn().mockResolvedValue({ total_paise: 69000 }), applyCode: vi.fn().mockResolvedValue({ discount_paise: 13800 }) },
  conversations: { open: vi.fn(), takeover: vi.fn().mockResolvedValue({}), reply: vi.fn().mockResolvedValue({}), handback: vi.fn() },
}));

const live = <T,>(value: T) => ({ data: value, isPending: false, isError: false, isSuccess: true, refetch: vi.fn() }) as never;

function wrap(ui: ReactNode, path = "/") {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/chats/:conversationId?" element={ui} />
            <Route path="*" element={ui} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  session.role = "shift_manager";
  vi.clearAllMocks();
});

describe("menu", () => {
  const item = (over: Partial<MenuItem>): MenuItem => ({
    id: "m1", name: "Cold Coffee", category: "Cold coffee", price_paise: 19000, prep_minutes: 5, veg: true, spice: 0,
    available: true, version: 1, updated_at: "", ...over,
  });

  test("two taps mark sold out; shift_manager sees no prices or prep", async () => {
    vi.mocked(data.useMenu).mockReturnValue(live([item({}), item({ id: "m2", name: "Masala Chai", category: "Tea", available: false })]));
    wrap(<MenuScreen />);
    expect(screen.getByText("1 item sold out today")).toBeInTheDocument();
    expect(screen.queryByText(/₹/)).not.toBeInTheDocument();
    const sw = screen.getByRole("switch", { name: "Cold Coffee available" });
    fireEvent.click(sw);
    expect(menu.setAvailable).not.toHaveBeenCalled();
    expect(screen.getByRole("switch", { name: "Tap again to mark Cold Coffee sold out" })).toHaveTextContent("Tap again to mark sold out");
    await act(async () => fireEvent.click(screen.getByRole("switch", { name: /Tap again/ })));
    expect(menu.setAvailable).toHaveBeenCalledWith("m1", false);
  });

  test("an armed switch expires after 5 s; back in stock is one tap; managers see price and prep", async () => {
    session.role = "manager";
    vi.useFakeTimers();
    try {
      vi.mocked(data.useMenu).mockReturnValue(live([item({}), item({ id: "m2", name: "Masala Chai", price_paise: 9000, available: false })]));
      wrap(<MenuScreen />);
      expect(screen.getByText(/5 min · ₹ 190/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("switch", { name: "Cold Coffee available" }));
      act(() => vi.advanceTimersByTime(ARM_MS + 10));
      expect(screen.getByRole("switch", { name: "Cold Coffee available" })).toBeInTheDocument();
      await act(async () => fireEvent.click(screen.getByRole("switch", { name: /Masala Chai sold out/ })));
      expect(menu.setAvailable).toHaveBeenCalledWith("m2", true);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("calls and bills", () => {
  const req = (over: Partial<data.LiveRequest>): data.LiveRequest => ({
    id: "r1", cafe_id: "cafe-1", table_session_id: "ts-1", conversation_id: "c1", order_id: null, kind: "help", status: "open",
    reason_tag: "asked", acknowledged_by: null, acknowledged_at: null, resolved_at: null, created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(), table: "T4", customerName: "Arjun", customerId: "cu-1", escalations: [], ackName: null,
    words: "Can someone come over?", ...over,
  });

  test("help open → Acknowledge; acknowledged → Resolve; low ratings hidden from shift_manager", async () => {
    vi.mocked(data.useOpenRequests).mockReturnValue(live([
      req({}),
      req({ id: "r2", kind: "allergy", status: "acknowledged", acknowledged_by: "u-1", ackName: "You", table: "T7" }),
      req({ id: "r3", kind: "low_rating", table: "T9" }),
    ]));
    wrap(<CallsScreen />);
    const help = screen.getByRole("article", { name: "Needs help, table T4" });
    expect(within(help).getByText("“Can someone come over?”")).toBeInTheDocument();
    await act(async () => fireEvent.click(within(help).getByRole("button", { name: "Acknowledge" })));
    expect(requests.ack).toHaveBeenCalledWith("r1");
    const allergy = screen.getByRole("article", { name: "Allergy question, table T7" });
    expect(within(allergy).getByText(/Acknowledged by You/)).toBeInTheDocument();
    expect(within(allergy).getByRole("button", { name: "Resolve" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /Low rating/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Low rating" })).not.toBeInTheDocument();
  });

  test("bill sheet shows the API's lines and total, applies a code, Bill delivered", async () => {
    vi.mocked(data.useOpenRequests).mockReturnValue(live([req({ kind: "bill", words: null })]));
    vi.mocked(data.useBill).mockReturnValue(live({
      table_session_id: "ts-1", closed_at: null, subtotal_paise: 69000, code: null, discount_paise: 0, total_paise: 69000,
      orders: [
        { id: "o1", number: 1041, total_paise: 51000, items: [{ name_snapshot: "Cold Coffee", qty: 1, line_paise: 51000 }] },
        { id: "o2", number: 1043, total_paise: 18000, items: [{ name_snapshot: "Masala Chai", qty: 2, line_paise: 18000 }] },
      ],
    }));
    wrap(<CallsScreen />);
    fireEvent.click(screen.getByRole("button", { name: /Open bill/ }));
    const sheet = screen.getByRole("dialog", { name: "Bill · T4" });
    expect(within(sheet).getByText("Total (incl. GST)")).toBeInTheDocument();
    expect(within(sheet).getByText("₹ 690")).toBeInTheDocument();
    fireEvent.change(within(sheet).getByLabelText("Offer code"), { target: { value: "back20-7kq" } });
    await act(async () => fireEvent.click(within(sheet).getByRole("button", { name: "Apply" })));
    expect(bills.applyCode).toHaveBeenCalledWith("ts-1", "BACK20-7KQ");
    await act(async () => fireEvent.click(within(sheet).getByRole("button", { name: "Bill delivered" })));
    expect(bills.deliver).toHaveBeenCalledWith("ts-1");
    expect(await screen.findByText("Bill delivered · T4 closed · ₹ 690")).toBeInTheDocument();
  });

  test("an invalid code shows the API's message and changes nothing", async () => {
    vi.mocked(data.useOpenRequests).mockReturnValue(live([req({ kind: "bill", words: null })]));
    vi.mocked(data.useBill).mockReturnValue(live({ table_session_id: "ts-1", closed_at: null, subtotal_paise: 18000, code: null,
      discount_paise: 0, total_paise: 18000, orders: [{ id: "o2", number: 1043, total_paise: 18000, items: [] }] }));
    vi.mocked(bills.applyCode).mockRejectedValueOnce(new Error("This isn't a valid code, or it has expired."));
    wrap(<CallsScreen />);
    fireEvent.click(screen.getByRole("button", { name: /Open bill/ }));
    fireEvent.change(screen.getByLabelText("Offer code"), { target: { value: "NOPE" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Apply" })));
    expect(screen.getByText("This isn't a valid code, or it has expired.")).toBeInTheDocument();
  });
});

describe("chats", () => {
  const thread = (over: Partial<data.Thread> = {}): data.Thread => ({
    conversation: { id: "c1", cafe_id: "cafe-1", customer_id: "cu-1", mode: "human", assigned_to: "u-1", table_session_id: "ts-1", updated_at: "" },
    customerName: "Riya", maskedPhone: "+91 99••• ••001", table: "T5", windowOpen: true, assignedName: "You",
    items: [
      { id: "i1", dir: "in", by: "customer", at: new Date().toISOString(), text: "Where is my coffee?", buttons: [], status: null },
      { id: "o1", dir: "out", by: "staff", at: new Date().toISOString(), text: "On its way", buttons: [], status: "failed" },
    ],
    ...over,
  });

  beforeEach(() => {
    vi.mocked(data.useChats).mockReturnValue(live([]));
  });

  test("quick replies fill the composer and never send; Send replies", async () => {
    vi.mocked(data.useThread).mockReturnValue(live(thread()));
    wrap(<ChatsScreen />, "/chats/c1");
    expect(screen.getByText("You're replying. The bot is paused.")).toBeInTheDocument();
    expect(screen.getByText("Not delivered")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Just 2 more minutes" }));
    expect(conversations.reply).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Reply")).toHaveValue("Just 2 more minutes");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
    expect(conversations.reply).toHaveBeenCalledWith("c1", "Just 2 more minutes");
  });

  test("bot mode warns that a reply pauses the bot; Take over", async () => {
    vi.mocked(data.useThread).mockReturnValue(live(thread({
      conversation: { ...thread().conversation, mode: "bot", assigned_to: null }, assignedName: null,
    })));
    wrap(<ChatsScreen />, "/chats/c1");
    expect(screen.getByText("Sending a reply pauses the bot for this chat.")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Take over" })));
    expect(conversations.takeover).toHaveBeenCalledWith("c1");
  });

  test("closed window disables the composer", () => {
    vi.mocked(data.useThread).mockReturnValue(live(thread({ windowOpen: false })));
    wrap(<ChatsScreen />, "/chats/c1");
    expect(screen.getByText("WhatsApp only allows a reply after the customer messages again.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
  });

  test("another teammate replying offers Take over", () => {
    vi.mocked(data.useThread).mockReturnValue(live(thread({ assignedName: "Meera", conversation: { ...thread().conversation, assigned_to: "u-2" } })));
    wrap(<ChatsScreen />, "/chats/c1");
    expect(screen.getByText("Meera is replying.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Take over" })).toBeInTheDocument();
  });
});

test("messages read as text, taps and buttons — never raw JSON", () => {
  expect(messageText("text", { text: { body: "hi" } })).toEqual({ text: "hi", buttons: [] });
  expect(messageText("interactive", { interactive: { button_reply: { title: "Menu" } } }).text).toBe("Tapped “Menu”");
  expect(messageText("interactive", { interactive: { list_reply: { title: "Cold Coffee" } } }).text).toBe("Picked “Cold Coffee”");
  expect(messageText("interactive", {
    interactive: { body: { text: "Your order" }, action: { buttons: [{ reply: { title: "Confirm" } }, { reply: { title: "Edit" } }] } },
  })).toEqual({ text: "Your order", buttons: ["Confirm", "Edit"] });
  expect(messageText("template", { template: { name: "order_status" } }).text).toBe("Template · order_status");
  expect(messageText("image", {}).text).toBe("(image)");
});
