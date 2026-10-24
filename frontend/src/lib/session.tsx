// Login and café context (docs/console.md#login-and-café-context).
import type { Session } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { setActiveCafe } from "./api/client";
import { supabase } from "./supabase";
import type { Membership, Role } from "./types";

const LAST_CAFE = "restro.lastCafe";

interface SessionState {
  ready: boolean;
  session: Session | null;
  memberships: Membership[];
  cafeId: string | null;
  cafeName: string;
  role: Role | null;
  displayName: string | null;
  userId: string | null;
  signIn(email: string, password: string): Promise<string | null>;
  signOut(): Promise<void>;
  switchCafe(cafeId: string): void;
}

const Ctx = createContext<SessionState | null>(null);

export function useSession(): SessionState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}

const RANK: Record<Role, number> = { shift_manager: 1, manager: 2, owner: 3 };
export const atLeast = (role: Role | null, min: Role): boolean => role !== null && RANK[role] >= RANK[min];

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: remembering the last café is a convenience only */
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [cafeId, setCafeId] = useState<string | null>(null);

  const loadMemberships = useCallback(async (s: Session | null) => {
    if (!s) {
      setMemberships([]);
      setCafeId(null);
      setActiveCafe(null);
      return;
    }
    const { data } = await supabase
      .from("memberships")
      .select("cafe_id, role, display_name, active, cafes(id, name, status, settings)")
      .eq("user_id", s.user.id)
      .eq("active", true);
    const rows = (data ?? []) as unknown as Membership[];
    setMemberships(rows);
    const last = safeGet(LAST_CAFE);
    const pick = rows.find((m) => m.cafe_id === last) ?? rows[0];
    setCafeId(pick?.cafe_id ?? null);
    setActiveCafe(pick?.cafe_id ?? null);
  }, []);

  useEffect(() => {
    supabase.auth.getSession().then(async ({ data }) => {
      setSession(data.session);
      await loadMemberships(data.session);
      setReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s);
      if (event === "SIGNED_IN" || event === "SIGNED_OUT") void loadMemberships(s);
    });
    return () => sub.subscription.unsubscribe();
  }, [loadMemberships]);

  const value = useMemo<SessionState>(() => {
    const m = memberships.find((x) => x.cafe_id === cafeId) ?? null;
    return {
      ready,
      session,
      memberships,
      cafeId,
      cafeName: m?.cafes?.name ?? "",
      role: m?.role ?? null,
      displayName: m?.display_name ?? null,
      userId: session?.user.id ?? null,
      async signIn(email, password) {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        return error ? "That email and password don't match." : null;
      },
      async signOut() {
        await supabase.auth.signOut();
      },
      switchCafe(id) {
        safeSet(LAST_CAFE, id);
        setCafeId(id);
        setActiveCafe(id);
      },
    };
  }, [ready, session, memberships, cafeId]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
