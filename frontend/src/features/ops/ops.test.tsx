// Performance dashboard (docs/OPS_PLAN.md): formatting, the token gate, the waterfall tree, tiles that link down.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import * as ops from "../../lib/ops";
import type { Span } from "../../lib/ops";
import { fmtMs, fmtN, fmtPct } from "./charts";
import { OpsLayout } from "./OpsLayout";
import { OpsRunTrace, tree } from "./Runs";

vi.mock("../../lib/ops", async (orig) => ({ ...(await orig<typeof import("../../lib/ops")>()), useOps: vi.fn() }));

const ok = <T,>(data: T) => ({ data, isPending: false, error: null, refetch: vi.fn() }) as never;

function wrap(ui: ReactNode, path: string, route: string) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={ui} /></Routes></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

test("units are always shown, large numbers grouped, missing values a dash", () => {
  expect(fmtMs(410)).toBe("410 ms");
  expect(fmtMs(2800)).toBe("2.80 s");
  expect(fmtMs(42_649)).toBe("42.6 s");
  expect(fmtMs(null)).toBe("—");
  expect(fmtN(1284000)).toBe("12,84,000");
  expect(fmtPct(0.9912)).toBe("99.1 %");
});

test("without a token the dashboard asks for one and keeps it in this tab only", () => {
  vi.mocked(ops.useOps).mockReturnValue({ data: undefined, isPending: true, error: null, refetch: vi.fn() } as never);
  wrap(<OpsLayout />, "/platform/ops/overview", "/platform/ops/*");
  expect(screen.getByRole("heading", { name: /Performance dashboard/ })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Ops token"), { target: { value: "tok-123" } });
  fireEvent.click(screen.getByRole("button", { name: "Open dashboard" }));
  expect(sessionStorage.getItem("restro.opsToken")).toBe("tok-123");
});

test("missing tables are a distinct message, not a blank page", () => {
  sessionStorage.setItem("restro.opsToken", "tok");
  vi.mocked(ops.useOps).mockReturnValue(ok({ tables_ok: false, tracing_enabled: true, git_sha: "abc1234", env: "local", features: {} }));
  wrap(<OpsLayout />, "/platform/ops/overview", "/platform/ops/*");
  expect(screen.getByText(/Tracing tables missing/)).toBeInTheDocument();
});

const span = (over: Partial<Span>): Span => ({
  span_id: 1, parent_id: null, name: "turn.load", level: 1, kind: "internal", start_ms: 0, duration_ms: 10, outcome: "ok",
  target: null, attrs: {}, error: null, ...over,
});

test("the trace tree nests by parent and collapses repeated calls into ×n", () => {
  const spans = [
    span({ span_id: 1, name: "turn.commit", duration_ms: 100 }),
    ...[2, 3, 4].map((id) => span({ span_id: id, parent_id: 1, name: "db.query", level: 3, kind: "db", start_ms: id * 10, duration_ms: 5, target: "orders" })),
    span({ span_id: 5, parent_id: 1, name: "db.query", level: 3, kind: "db", start_ms: 60, duration_ms: 5, target: "wa_messages" }),
  ];
  const [root] = tree(spans);
  expect(root!.children).toHaveLength(2);
  expect(root!.children[0]).toMatchObject({ name: "db.query", target: "orders", repeated: 3 });
  expect(root!.children[0]!.attrs._ms).toEqual([5, 5, 5]);
});

test("run trace: outcome, failure reason, attempts and a waterfall row per stage", () => {
  vi.mocked(ops.useOps).mockReturnValue(ok({
    run: { run_id: "r1", name: "turn", subject_type: "pending_work", subject_id: "w1", attempt: 2, outcome: "FAILED", duration_ms: 812,
      queue_wait_ms: 40, service: "worker", instance: "worker-1", git_sha: "abc1234", config_hash: "c0ffee", counts: { reply_ms: 900 },
      dep_calls: { db: 4 }, stage_ms: { "turn.commit": 700 }, degradations: [], error_stage: "turn.commit", error_code: "DB_ERROR",
      failure_reason: "UniqueViolationError: duplicate key", input_size: 1, input_size_unit: "messages", upstream_trace: null },
    spans: [span({ span_id: 1, name: "turn.commit", duration_ms: 700, outcome: "failed", error: "UniqueViolationError" })],
    events: [{ seq: 1, at_ms: 650, level: "ERROR", span_name: "turn.commit", logger: null, message: "turn_failed", detail: null }],
    attempts: [
      { run_id: "r0", attempt: 1, started_at: "2026-10-25T01:00:00Z", duration_ms: 500, outcome: "RETRYING", error_stage: "turn.commit", error_code: "DB_ERROR", failure_reason: "x" },
      { run_id: "r1", attempt: 2, started_at: "2026-10-25T01:00:02Z", duration_ms: 812, outcome: "FAILED", error_stage: "turn.commit", error_code: "DB_ERROR", failure_reason: "x" },
    ],
    stage_p50: { "turn.commit": 200 }, upstream: null, downstream: [],
  }));
  wrap(<OpsRunTrace />, "/platform/ops/runs/r1", "/platform/ops/runs/:runId");
  expect(document.querySelector(".trace-outcome strong")).toHaveTextContent("812 ms");
  expect(screen.getByText(/UniqueViolationError: duplicate key/)).toBeInTheDocument();
  expect(screen.getByText("3.5× p50")).toBeInTheDocument();
  expect(screen.getByText("Attempts (2)")).toBeInTheDocument();
  expect(screen.getByText("turn_failed")).toBeInTheDocument();
  expect(screen.getAllByText("Failed").length).toBeGreaterThan(0); // status colour always with its label
});
