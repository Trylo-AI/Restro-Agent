// Offline detection: api or Realtime unreachable for more than 5 s, or the browser offline → Offline banner.
import { useEffect, useRef, useState } from "react";

import { API_URL } from "./api/client";
import { supabase } from "./supabase";
import { report } from "./telemetry";

const PING_MS = 1_500; // banner shows 5–6.5 s after the api or Realtime drops
const GRACE_MS = 5_000;

export function useConnection(): { offline: boolean; retry: () => void; cameBackAt: number | null } {
  const [offline, setOffline] = useState(false);
  const [cameBackAt, setCameBackAt] = useState<number | null>(null);
  const downSince = useRef<number | null>(null);
  const wasOffline = useRef(false);

  const check = async () => {
    let ok = navigator.onLine;
    if (ok) {
      try {
        const r = await fetch(`${API_URL}/healthz`, { cache: "no-store" });
        ok = r.ok;
      } catch {
        ok = false;
      }
    }
    const realtimeOk = supabase.realtime.isConnected() || supabase.getChannels().length === 0;
    ok = ok && realtimeOk;
    const now = Date.now();
    if (ok) {
      downSince.current = null;
      if (wasOffline.current) {
        wasOffline.current = false;
        setCameBackAt(now);
        report("realtime_reconnect");
      }
      setOffline(false);
    } else {
      downSince.current = downSince.current ?? now;
      if (now - downSince.current > GRACE_MS && !wasOffline.current) {
        wasOffline.current = true;
        setOffline(true);
        report("offline_banner");
      }
    }
  };

  useEffect(() => {
    const id = window.setInterval(() => void check(), PING_MS);
    const onChange = () => void check();
    window.addEventListener("online", onChange);
    window.addEventListener("offline", onChange);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("online", onChange);
      window.removeEventListener("offline", onChange);
    };
  }, []);

  return { offline, retry: () => void check(), cameBackAt };
}
