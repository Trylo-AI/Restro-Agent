// Customer profile panel (docs/console.md#customer-profile, "Done when").
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { ToastProvider } from "../../app/toasts";
import { ApiError } from "../../lib/api/client";
import { useCustomerProfile, type CustomerProfile } from "../../lib/data";
import { customers } from "../../lib/mutations";
import type { CustomerNote, Preference } from "../../lib/types";
import { CustomerProfileBody } from "./CustomerProfilePanel";

vi.mock("../../lib/session", async (orig) => ({
  ...(await orig<typeof import("../../lib/session")>()),
  useSession: () => ({ cafeId: "cafe-1", role: "shift_manager", userId: "u-1" }),
}));
vi.mock("../../lib/data", async (orig) => ({
  ...(await orig<typeof import("../../lib/data")>()),
  useCustomerProfile: vi.fn(),
}));
vi.mock("../../lib/mutations", async (orig) => ({
  ...(await orig<typeof import("../../lib/mutations")>()),
  customers: { addNote: vi.fn(), undoNote: vi.fn(), removePreference: vi.fn() },
}));

const pref = (kind: Preference["kind"], value: string): Preference =>
  ({ id: `${kind}-${value}`, customer_id: "cu-1", kind, value, source: "staff_note", note_id: null });

const note = (over: Partial<CustomerNote & { author: string }>): CustomerNote & { author: string } => ({
  id: "n1", customer_id: "cu-1", author_id: "u-1", author: "You", text: "She is allergic to peanuts and likes oat milk",
  ai_status: "applied", ai_ops: [{ op: "add", kind: "allergy", value: "peanuts" }, { op: "set", kind: "milk", value: "oat" }],
  ai_summary: null, undone_at: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ...over,
});

function profile(over: Partial<CustomerProfile> = {}): CustomerProfile {
  return {
    card: {
      customer_id: "cu-1", first_name: "Riya", masked_phone: "+91 98••• ••421", visits: 6, first_seen_at: "2026-08-01T10:00:00Z",
      last_visit_at: "2026-10-12T10:00:00Z", top_items: ["Filter Coffee", "Masala Dosa"], segment: "regular", avg_rating: 4.5, rating_count: 2,
    },
    prefs: [pref("allergy", "peanuts"), pref("dislike", "onion"), pref("diet", "veg"), pref("milk", "oat")],
    notes: [note({})],
    feedback: [{ id: "f1", rating: 2, text: "Coffee was cold", created_at: "2026-10-12T11:00:00Z", number: 1042 }],
    complaints: [{ id: "f1", at: "2026-10-12T11:00:00Z", label: "2★ on #1042", words: "Coffee was cold" }],
    recent: [{ id: "o1", number: 1042, status: "CANCELLED", total_paise: 12000, created_at: "2026-10-12T10:00:00Z",
      items: [{ name_snapshot: "Filter Coffee", qty: 2 }] }],
    ...over,
  };
}

function show(p: CustomerProfile) {
  vi.mocked(useCustomerProfile).mockReturnValue({ data: p, isPending: false, isError: false, refetch: vi.fn() } as never);
  return render(
    <QueryClientProvider client={new QueryClient()}><ToastProvider><CustomerProfileBody customerId="cu-1" /></ToastProvider></QueryClientProvider>,
  );
}

test("the at-a-glance box: name, visit, masked phone, last visit, usual dishes, allergy, dislikes, diet, prefers", () => {
  show(profile());
  const box = screen.getByRole("region", { name: "At a glance" });
  expect(within(box).getByText("Riya")).toBeInTheDocument();
  expect(within(box).getByText("6th visit")).toBeInTheDocument();
  expect(within(box).getByText(/\+91 98••• ••421/)).toBeInTheDocument();
  expect(within(box).getByText(/Last visit 12 Oct/)).toBeInTheDocument();
  expect(within(box).getByText(/Usually: Filter Coffee, Masala Dosa/)).toBeInTheDocument();
  expect(within(box).getByText("Allergy")).toBeInTheDocument();
  expect(within(box).getByText(/peanuts/)).toBeInTheDocument();
  expect(within(box).getByText(/onion/)).toBeInTheDocument();
  expect(within(box).getByText(/Milk: oat/)).toBeInTheDocument();
});

test("chip × removes one value", async () => {
  vi.mocked(customers.removePreference).mockResolvedValue([] as never);
  show(profile());
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Remove onion" })));
  expect(customers.removePreference).toHaveBeenCalledWith("cu-1", "dislike", "onion");
});

test("details: feedback, complaints with words, recent orders with cancelled struck through", () => {
  show(profile());
  expect(screen.getByText("2★ on #1042")).toBeInTheDocument();
  expect(screen.getAllByText("“Coffee was cold”")).toHaveLength(2);
  expect(screen.getByText("#1042", { selector: ".plist li.struck .mono" })).toBeInTheDocument();
});

test("comments show what the AI changed; Undo 409 tells staff to use the box", async () => {
  vi.mocked(customers.undoNote).mockRejectedValue(new ApiError(409, "version_conflict", "conflict"));
  show(profile());
  expect(screen.getByText("Added allergy: peanuts")).toBeInTheDocument();
  expect(screen.getByText("Milk: oat", { selector: ".note-result li" })).toBeInTheDocument();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Undo" })));
  expect(await screen.findByText("Changed again since — remove it from the box instead")).toBeInTheDocument();
});

test("pending, no-change and failed comments", () => {
  show(profile({ notes: [
    note({ id: "a", ai_status: "pending", ai_ops: [] }),
    note({ id: "b", ai_status: "no_changes", ai_ops: [] }),
    note({ id: "c", ai_status: "failed", ai_ops: [] }),
  ] }));
  expect(screen.getByText("Updating profile…")).toBeInTheDocument();
  expect(screen.getByText("No profile changes")).toBeInTheDocument();
  expect(screen.getByText("Couldn't read this — profile unchanged")).toBeInTheDocument();
});

test("a first-visit, empty profile looks intentional", () => {
  show(profile({ card: { ...profile().card!, visits: 1, top_items: null, segment: "new" }, prefs: [], notes: [], feedback: [], complaints: [], recent: [] }));
  expect(screen.getByText("First visit")).toBeInTheDocument();
  expect(screen.getByText(/No preferences yet/)).toBeInTheDocument();
  expect(screen.getByText("No comments yet.")).toBeInTheDocument();
});

test("add a comment posts the text", async () => {
  vi.mocked(customers.addNote).mockResolvedValue({} as never);
  show(profile());
  fireEvent.change(screen.getByLabelText("Add a comment"), { target: { value: "Prefers window seat" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save" })));
  expect(customers.addNote).toHaveBeenCalledWith("cu-1", "Prefers window seat");
});
