// Menu editing (docs/console.md#menu "Done when"): roles, paise, allergen states, 409 Reload, import errors.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { ToastProvider } from "../../app/toasts";
import { ApiError } from "../../lib/api/client";
import * as data from "../../lib/data";
import { menu } from "../../lib/mutations";
import type { MenuItem } from "../../lib/types";
import { ImportDialog, toPaise } from "./MenuEditor";
import { MenuScreen } from "./MenuScreen";

const session = vi.hoisted(() => ({ role: "manager" as string }));
vi.mock("../../lib/session", async (orig) => ({
  ...(await orig<typeof import("../../lib/session")>()),
  useSession: () => ({ cafeId: "cafe-1", role: session.role, userId: "u-1", cafeName: "Kaapi House" }),
}));
vi.mock("../../lib/data", async (orig) => ({ ...(await orig<typeof import("../../lib/data")>()), useMenu: vi.fn(), useItemAllergens: vi.fn() }));
vi.mock("../../lib/mutations", async (orig) => ({
  ...(await orig<typeof import("../../lib/mutations")>()),
  menu: { setAvailable: vi.fn(), create: vi.fn().mockResolvedValue({}), update: vi.fn().mockResolvedValue({}), importCsv: vi.fn() },
}));

const live = <T,>(value: T) => ({ data: value, isPending: false, isError: false, refetch: vi.fn() }) as never;
const coffee: MenuItem = { id: "m1", name: "Cold Coffee", category: "Cold coffee", price_paise: 19000, prep_minutes: 5, veg: true,
  spice: 0, available: true, version: 7, updated_at: "", description: null };

function wrap(ui: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}><ToastProvider><MemoryRouter>{ui}</MemoryRouter></ToastProvider></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(data.useMenu).mockReturnValue(live([coffee]));
  vi.mocked(data.useItemAllergens).mockReturnValue(live({ milk: "contains" }));
});

test("rupees become integer paise; anything else is refused", () => {
  expect(toPaise("190")).toBe(19000);
  expect(toPaise("₹ 1,234.5")).toBe(123450);
  expect(toPaise("12.345")).toBeNull();
  expect(toPaise("0")).toBeNull();
  expect(toPaise("abc")).toBeNull();
});

test("shift_manager sees no New, Edit or Import; manager no Import; owner all", () => {
  session.role = "shift_manager";
  const { unmount } = wrap(<MenuScreen />);
  expect(screen.queryByRole("button", { name: /New item/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit Cold Coffee" })).not.toBeInTheDocument();
  unmount();
  session.role = "manager";
  const m = wrap(<MenuScreen />);
  expect(screen.getByRole("button", { name: /New item/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Import CSV/ })).not.toBeInTheDocument();
  m.unmount();
  session.role = "owner";
  wrap(<MenuScreen />);
  expect(screen.getByRole("button", { name: /Import CSV/ })).toBeInTheDocument();
});

test("edit saves paise, prep, veg, spice and every allergen state with the version", async () => {
  session.role = "manager";
  wrap(<MenuScreen />);
  fireEvent.click(screen.getByRole("button", { name: "Edit Cold Coffee" }));
  const sheet = screen.getByRole("dialog", { name: "Edit Cold Coffee" });
  fireEvent.change(within(sheet).getByLabelText("Price (₹, incl. GST)"), { target: { value: "210.50" } });
  fireEvent.click(within(within(sheet).getByRole("radiogroup", { name: "Spice" })).getByRole("radio", { name: "🌶🌶" }));
  fireEvent.click(within(within(sheet).getByRole("radiogroup", { name: "Peanuts" })).getByRole("radio", { name: "Free from" }));
  await act(async () => fireEvent.click(within(sheet).getByRole("button", { name: "Save" })));
  const [id, body, version] = vi.mocked(menu.update).mock.calls[0]!;
  expect(id).toBe("m1");
  expect(version).toBe(7);
  expect(body).toMatchObject({ price_paise: 21050, spice: 2, prep_minutes: 5, veg: true });
  const states = Object.fromEntries(body.allergens!.map((a) => [a.allergen, a.state]));
  expect(states).toMatchObject({ milk: "contains", peanuts: "free_from", gluten: "unknown" });
  expect(Object.keys(states)).toHaveLength(11);
});

test("409 says someone else changed it and offers Reload", async () => {
  session.role = "manager";
  vi.mocked(menu.update).mockRejectedValueOnce(new ApiError(409, "version_conflict", "Someone else changed this item."));
  wrap(<MenuScreen />);
  fireEvent.click(screen.getByRole("button", { name: "Edit Cold Coffee" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save" })));
  expect(screen.getByText("Someone else changed this item.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

test("import shows every rejected row and changes nothing", async () => {
  vi.mocked(menu.importCsv).mockRejectedValueOnce(new ApiError(400, "bad_request", "The file has 1 problem; nothing was imported.",
    { rows: [{ row: 4, column: "price", message: "Price must be in rupees, more than 0, at most 2 decimals." }] }));
  wrap(<ImportDialog onClose={vi.fn()} />);
  const file = new File(["name,category\n"], "menu.csv", { type: "text/csv" });
  Object.defineProperty(file, "text", { value: () => Promise.resolve("name,category\n") });
  fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [file] } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: /Import/ })));
  expect(screen.getByText("The file has 1 problem; nothing was imported.")).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "Price must be in rupees, more than 0, at most 2 decimals." })).toBeInTheDocument();
});
