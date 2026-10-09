import type { AuthConfig, Me } from "@agentledger/contracts";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { api } from "../api";
import { session } from "./session";

/** What the router's guards read: who is signed in (if anyone) and how this deployment signs people in. */
export interface AuthSnapshot {
  readonly me: Me | null;
  readonly config: AuthConfig;
}

/**
 * The snapshot the router context holds. It is updated in place when the session changes, so a guard that runs
 * right after a sign-out (or a 401) already sees the new answer; React state follows for rendering.
 */
export interface AuthStore extends AuthSnapshot {
  setMe(me: Me | null): void;
}

export function createAuthStore(initial: { me: Me | null; config: AuthConfig }): AuthStore {
  let current = initial.me;
  return {
    config: initial.config,
    get me() {
      return current;
    },
    setMe(next) {
      current = next;
    },
  };
}

export interface AuthValue {
  me: Me | null;
  config: AuthConfig;
  /** Store a fresh session token and load the account behind it. */
  signIn(token: string): Promise<Me>;
  /** POST /api/auth/logout (best effort), then forget everything. */
  signOut(): Promise<void>;
  /** GET /api/me again (keeps the session alive; refreshes roles and freshness). */
  refresh(): Promise<Me | null>;
  /** The API said 401: forget the session without calling the API. */
  endSession(): void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ store, children }: { store: AuthStore; children: ReactNode }) {
  const [me, setMeState] = useState<Me | null>(store.me);
  const config = store.config;

  const setMe = useCallback(
    (who: Me | null) => {
      store.setMe(who);
      setMeState(who);
    },
    [store],
  );

  const signIn = useCallback(
    async (token: string) => {
      session.set(token);
      const who = await api.me();
      setMe(who);
      return who;
    },
    [setMe],
  );

  const signOut = useCallback(async () => {
    try {
      await api.auth.logout();
    } catch {
      /* the session is forgotten here regardless */
    }
    session.clear();
    setMe(null);
  }, [setMe]);

  const refresh = useCallback(async () => {
    if (!session.get()) return null;
    try {
      const who = await api.me();
      setMe(who);
      return who;
    } catch {
      return null;
    }
  }, [setMe]);

  const endSession = useCallback(() => {
    session.clear();
    setMe(null);
  }, [setMe]);

  const value = useMemo<AuthValue>(
    () => ({ me, config, signIn, signOut, refresh, endSession }),
    [me, config, signIn, signOut, refresh, endSession],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth needs an AuthProvider");
  return ctx;
}

/** The signed-in account, for screens behind the `_app` guard (which guarantees one). */
export function useMe(): Me {
  const { me } = useAuth();
  if (!me) throw new Error("useMe called outside a signed-in route");
  return me;
}

/** What a change of this key means: a different person, role or set of engagements, so route guards must re-run. */
export function identityKey(me: Me | null): string {
  return me ? `${me.id}:${me.base_role}:${(me.engaged ?? []).join(",")}:${me.client_id ?? ""}` : "anonymous";
}
