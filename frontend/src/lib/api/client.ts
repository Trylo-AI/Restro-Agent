// Typed client for every write (contracts/openapi.yaml). Adds the user's JWT and the active café on each call.
import createClient, { type Middleware } from "openapi-fetch";

import { supabase } from "../supabase";
import type { paths } from "./schema";

export const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

let activeCafeId: string | null = null;
export function setActiveCafe(id: string | null): void {
  activeCafeId = id;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: Record<string, unknown>,
  ) {
    super(message);
  }
}

const auth: Middleware = {
  async onRequest({ request }) {
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    if (activeCafeId) request.headers.set("X-Cafe-Id", activeCafeId);
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: API_URL });
api.use(auth);

type ApiResult<T> = { data?: T; error?: unknown; response: Response };

/** Unwraps an openapi-fetch result: returns data, or throws ApiError with the contract's error body. */
export async function call<T>(p: Promise<ApiResult<T>>): Promise<T> {
  let res: ApiResult<T>;
  try {
    res = await p;
  } catch {
    throw new ApiError(0, "network", "Can't reach Restro. Check the connection and try again.");
  }
  if (res.response.ok) return res.data as T;
  const body = (res.error ?? {}) as { error?: { code?: string; message?: string; details?: Record<string, unknown> } };
  throw new ApiError(
    res.response.status,
    body.error?.code ?? "internal",
    body.error?.message ?? "Something went wrong. Please try again.",
    body.error?.details,
  );
}

export function newIdempotencyKey(): string {
  return `${Date.now().toString(36)}-${crypto.randomUUID()}`;
}

/** The contract's required `X-Cafe-Id` header (the middleware sets it too; this satisfies the types). */
export function cafeHeader(): { header: { "X-Cafe-Id": string } } {
  return { header: { "X-Cafe-Id": activeCafeId ?? "" } };
}
