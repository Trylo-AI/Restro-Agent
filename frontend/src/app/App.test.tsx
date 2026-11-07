import { configure, render, screen, within } from "@testing-library/react";

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

// Rendering the whole landing page is slow under a busy full run; give these tests room beyond the 5 s default.
vi.setConfig({ testTimeout: 20_000 });
configure({ asyncUtilTimeout: 5_000 }); // findBy* waits; a lazy screen can take over 1 s to load mid-suite

// The landing page is a lazy chunk; load it once up front so a cold import under a busy full run can't time out a test.
beforeAll(async () => { await import("../features/landing/Landing"); }, 30_000);

afterEach(() => {
  signedIn.role = null;
  window.history.pushState({}, "", "/");
});

test("signed out users see the landing page at /", async () => {
  render(<App />);
  expect(await screen.findByRole("heading", { level: 1, name: /Every table served/ }, { timeout: 5000 })).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "Log in" })[0]).toHaveAttribute("href", "/login");
  expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
});

test("the landing page lists all four problem areas", async () => {
  render(<App />);
  const tabs = await screen.findAllByRole("tab", {}, { timeout: 5000 });
  expect(tabs.map((t) => t.firstChild?.textContent)).toEqual(["Choosing", "Ordering", "At the table", "Coming back"]);
});

test("the landing page shows how to reach us", async () => {
  render(<App />);
  await screen.findByRole("heading", { level: 1, name: /Every table served/ }, { timeout: 5000 });
  expect(screen.getAllByRole("link", { name: /team@mehmanawazi\.com/ })[0]).toHaveAttribute("href", "mailto:team@mehmanawazi.com");
  expect(screen.getAllByRole("link", { name: /99107 72433/ })[0]).toHaveAttribute("href", "tel:+919910772433");
  expect(screen.getAllByRole("link", { name: /Book a demo/ })[0]?.getAttribute("href")).toMatch(/^https:\/\/wa\.me\/919910772433\?text=/);
});

test("the landing page has no footer and no delete-your-data link, and offers a sound toggle", async () => {
  const { container } = render(<App />);
  await screen.findByRole("heading", { level: 1, name: /Every table served/ }, { timeout: 5000 });
  expect(container.querySelector("footer")).toBeNull();
  expect(screen.queryByRole("link", { name: /delete your data/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /demo sounds/ })).toBeInTheDocument();
});

test("signed out users see the login screen at /login", async () => {
  window.history.pushState({}, "", "/login");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Sign in to the console" })).toBeInTheDocument();
  expect(screen.getByLabelText("Email")).toBeInTheDocument();
});

test("a signed-out deep link shows the login form and keeps its path", async () => {
  window.history.pushState({}, "", "/orders");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Sign in to the console" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/orders");
});

test.each([
  ["/login", "shift_manager", "/orders"],
  ["/login", "manager", "/orders"],
  ["/", "shift_manager", "/orders"],
])("signed in at %s as %s goes to %s", async (path, role, to) => {
  at(path, role);
  expect(await screen.findByText("Orders screen")).toBeInTheDocument();
  expect(window.location.pathname).toBe(to);
});

test("signed in owner at /login goes to Today", async () => {
  at("/login", "owner");
  await screen.findByRole("heading", { level: 1, name: "Today" });
  expect(window.location.pathname).toBe("/today");
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
