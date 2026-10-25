// Performance dashboard reads (/api/ops/*, docs/OPS_PLAN.md). Not part of contracts/openapi.yaml: the ops API is
// internal tooling defined by the Performance Dashboard Standard. Access is the X-Ops-Token (platform login later);
// the token lives in this tab's sessionStorage only.
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { API_URL } from "./api/client";

const KEY = "restro.opsToken";

export function opsToken(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setOpsToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(KEY, token);
    else sessionStorage.removeItem(KEY);
  } catch {
    // private mode: the token lasts only as long as this page
  }
}

export class OpsError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function opsGet<T>(path: string, params: Record<string, string | number | undefined | null> = {}): Promise<T> {
  const url = new URL(`${API_URL}/api/ops${path}`);
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  let res: Response;
  try {
    res = await fetch(url, { headers: { "X-Ops-Token": opsToken() ?? "" } });
  } catch {
    throw new OpsError(0, "Can't reach the api.");
  }
  if (res.status === 401) throw new OpsError(401, "The ops token is missing or wrong.");
  if (res.status === 404) throw new OpsError(404, "The dashboard API is off: set OPS_DASHBOARD_TOKEN in backend/.env.");
  if (!res.ok) throw new OpsError(res.status, "The dashboard query failed.");
  return (await res.json()) as T;
}

export async function opsPost(path: string, body: unknown): Promise<void> {
  const res = await fetch(`${API_URL}/api/ops${path}`, {
    method: "POST", headers: { "X-Ops-Token": opsToken() ?? "", "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new OpsError(res.status, "Couldn't save that.");
}

export const RANGES = { "1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400 } as const;
export type Range = keyof typeof RANGES;

/** The time range and service live in the URL, so a view can be pasted to a colleague. */
export function useOpsWindow(): { range: Range; service: string; params: { from: string; to: string; service?: string } } {
  const [sp] = useSearchParams();
  const range = (sp.get("range") as Range | null) ?? "24h";
  const service = sp.get("service") ?? "";
  const seconds = RANGES[range] ?? RANGES["24h"];
  // Rounded to the minute so the query key is stable between renders.
  const to = new Date(Math.ceil(Date.now() / 60_000) * 60_000);
  const from = new Date(to.getTime() - seconds * 1000);
  return { range, service, params: { from: from.toISOString(), to: to.toISOString(), service: service || undefined } };
}

export function useOps<T>(path: string, params: Record<string, string | number | undefined | null> = {}, opts: { refetchMs?: number; enabled?: boolean } = {}) {
  return useQuery<T, OpsError>({
    queryKey: ["ops", path, params],
    queryFn: () => opsGet<T>(path, params),
    refetchInterval: opts.refetchMs,
    enabled: opts.enabled ?? true,
    retry: (n, e) => e.status !== 401 && e.status !== 404 && n < 1,
    staleTime: 15_000,
  });
}

// ── shapes (loose: the ops API is internal) ─────────────────────────────

export interface Pct { p50: number | null; p90?: number | null; p95: number | null; p99?: number | null; max?: number | null; n: number }
export interface Outcomes {
  units: number; succeeded: number; degraded: number; failed: number; rejected: number; cancelled: number; superseded: number;
  retrying: number; retried: number; recovered: number; runs: number; success_rate: number | null; degraded_rate: number | null;
}
export interface RunRow {
  run_id: string; service: string; kind: string; name: string; subject_type: string | null; subject_id: string | null;
  tenant_id: string | null; attempt: number; started_at: string; duration_ms: number | null; queue_wait_ms: number | null;
  outcome: string; http_status: number | null; error_stage: string | null; error_code: string | null;
  failure_fingerprint: string | null; labels: Record<string, string>; counts: Record<string, number>;
  current_stage: string | null; span_count: number; degradations: number;
}
export interface Span {
  span_id: number; parent_id: number | null; name: string; level: number; kind: string; start_ms: number; duration_ms: number;
  outcome: string; target: string | null; attrs: Record<string, unknown>; error: string | null;
}
export interface Finding {
  key: string; detector: string; severity: "high" | "medium" | "low"; title: string; where: Record<string, unknown>;
  evidence: Record<string, unknown>; estimated_saving: { ms_per_day: number | null }; example_run_ids: string[];
  first_seen: string | null; confidence: string; fix: string; dismissed?: { note: string | null };
}
