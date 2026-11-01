// Live hook: one Realtime channel per query, event bursts coalesced, nothing missed (docs/data-and-api.md#realtime).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";

import { COALESCE_MS, useLive } from "./live";

type Handler = () => void;
const channels: { name: string; handlers: { event: string; table: string; fn: Handler }[]; removed: boolean }[] = [];

vi.mock("./supabase", () => ({
  supabase: {
    channel: (name: string) => {
      const ch = { name, handlers: [] as { event: string; table: string; fn: Handler }[], removed: false };
      channels.push(ch);
      const api = {
        on: (_: string, f: { event: string; table: string }, fn: Handler) => { ch.handlers.push({ event: f.event, table: f.table, fn }); return api; },
        subscribe: () => api,
        _ch: ch,
      };
      return api;
    },
    removeChannel: (api: { _ch: { removed: boolean } }) => { api._ch.removed = true; return Promise.resolve("ok"); },
  },
}));

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  return { spy, wrapper };
}

const opts = (fetcher: () => Promise<number>) => ({
  key: ["orders", "cafe-1"], enabled: true, cafeId: "cafe-1", fetcher,
  tables: ["orders" as const, { table: "wa_messages" as const, event: "INSERT" as const }],
});

beforeEach(() => {
  channels.length = 0;
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});
afterEach(() => vi.useRealTimers());

test("hooks reading the same query share one channel, removed when the last one unmounts", () => {
  const { wrapper } = setup();
  const fetcher = () => Promise.resolve(1);
  const a = renderHook(() => useLive(opts(fetcher)), { wrapper });
  const b = renderHook(() => useLive(opts(fetcher)), { wrapper });
  const open = channels.filter((c) => !c.removed);
  expect(open).toHaveLength(1);
  expect(open[0]!.handlers.map((h) => `${h.table}:${h.event}`)).toEqual(["orders:*", "wa_messages:INSERT"]);
  a.unmount();
  expect(open[0]!.removed).toBe(false);
  b.unmount();
  expect(open[0]!.removed).toBe(true);
});

test("the first event refetches at once; a burst becomes one more refetch, after the burst", () => {
  const { spy, wrapper } = setup();
  renderHook(() => useLive(opts(() => Promise.resolve(1))), { wrapper });
  const fire = channels.find((c) => !c.removed)!.handlers[0]!.fn;
  spy.mockClear();

  act(() => fire());
  expect(spy).toHaveBeenCalledTimes(1); // a new order shows without waiting

  for (let i = 0; i < 10; i++) act(() => fire());
  expect(spy).toHaveBeenCalledTimes(1);
  act(() => vi.advanceTimersByTime(COALESCE_MS));
  expect(spy).toHaveBeenCalledTimes(2); // the last event in the burst is never lost

  act(() => vi.advanceTimersByTime(COALESCE_MS * 5));
  expect(spy).toHaveBeenCalledTimes(2);
});
