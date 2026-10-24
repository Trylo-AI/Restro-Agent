// Console connection events → POST /telemetry/console (metrics only, no content). Best effort, batched.
import { api, cafeHeader } from "./api/client";

type Kind = "realtime_disconnect" | "realtime_reconnect" | "offline_banner";
let queue: { kind: Kind; at: string }[] = [];
let timer: number | null = null;

export function report(kind: Kind): void {
  queue.push({ kind, at: new Date().toISOString() });
  if (timer === null) {
    timer = window.setTimeout(() => {
      const events = queue.slice(0, 50);
      queue = [];
      timer = null;
      void api.POST("/telemetry/console", { params: cafeHeader(), body: { events } }).catch(() => undefined);
    }, 2_000);
  }
}
