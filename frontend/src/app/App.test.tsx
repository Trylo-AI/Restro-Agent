import { render, screen, within } from "@testing-library/react";

import { App } from "./App";

// null: the real session (signed out). Otherwise a signed-in member with this role.
const signedIn = vi.hoisted(() => ({ role: null as string | null }));
vi.mock("../lib/session", async (orig) => {
  const real = await orig<typeof import("../lib/session")>();
  return {
    ...real,
    useSession: () => (signedIn.role === null ? real.useSession() : {
      ready: true, loadError: false, session: {}, memberships: [], cafeId: "cafe-1", cafeName: "Kaapi House",
      role: signedIn.role, displayName: "Riya", userId: "u-1", signIn: vi.fn(), signOut: vi.fn(), switchCafe: vi.fn(), retry: vi.fn(),
    }),
  };
});
vi.mock("../lib/data", async (orig) => {
  const none = () => ({ data: undefined, isPending: true, isError: false });
  return {
    ...(await orig<typeof import("../lib/data")>()),
    useCafe: none, useLiveOrders: none, useOpenRequests: none, useChats: none, useNotifications: none,
  };
});
vi.mock("../lib/connection", () => ({ useConnection: () => ({ offline: false, retry: vi.fn(), cameBackAt: null }) }));
vi.mock("../features/orders/OrdersScreen", () => ({ OrdersScreen: () => <p>Orders screen</p> }));

function at(path: string, role: string | null) {
  signedIn.role = role;
  window.history.pushState({}, "", path);
  return render(<App />);
}

afterEach(() => {
  signedIn.role = null;
  window.history.pushState({}, "", "/");
});

test("signed out users see the login screen", async () => {
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Sign in to the console" })).toBeInTheDocument();
  expect(screen.getByLabelText("Email")).toBeInTheDocument();
});

test("shift_manager sees Redeem offer in the nav and the tab bar", async () => {
  const { container } = at("/orders", "shift_manager");
  expect(await screen.findByText("Orders screen")).toBeInTheDocument();
  const sidebar = container.querySelector<HTMLElement>(".sidebar")!;
  expect(within(sidebar).getByRole("link", { name: "Redeem offer" })).toHaveAttribute("href", "/redeem");
  const tabbar = container.querySelector<HTMLElement>(".tabbar")!;
  expect(within(tabbar).getByRole("link", { name: "Redeem" })).toHaveAttribute("href", "/redeem");
  expect(within(tabbar).getAllByRole("link")).toHaveLength(5);
  expect(tabbar.style.gridTemplateColumns).toBe("repeat(5, 1fr)");
});

test("Audit log nav only for owner", async () => {
  const owner = at("/orders", "owner");
  expect(await screen.findByText("Orders screen")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Audit log" })).toHaveAttribute("href", "/audit");
  owner.unmount();
  for (const role of ["manager", "shift_manager"]) {
    const r = at("/orders", role);
    expect(await screen.findByText("Orders screen")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit log" })).not.toBeInTheDocument();
    r.unmount();
  }
});

test("/audit as manager redirects to /orders", async () => {
  at("/audit", "manager");
  expect(await screen.findByText("Orders screen")).toBeInTheDocument();
  expect(window.location.pathname).toBe("/orders");
  expect(screen.getByRole("heading", { level: 1, name: "Live orders" })).toBeInTheDocument();
});
