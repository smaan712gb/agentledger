/**
 * The session token: kept in memory and in sessionStorage (one browser tab, gone when it closes). Never
 * localStorage, never a URL, never a cookie the script can read. There is no refresh token: the API's own rules
 * apply (30 minutes idle, 12 hours absolute) and a 401 ends the session here. The follow-up that moves the token
 * into an HttpOnly cookie is recorded in docs/WEB.md.
 */

const STORAGE_KEY = "agentledger.session";
const RETURN_KEY = "agentledger.return_to";
const PENDING_KEY = "agentledger.pending_action";

type Listener = () => void;

let token: string | null = null;
let loaded = false;
const listeners = new Set<Listener>();

function read(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the in-memory copy still serves this page */
  }
}

export const session = {
  get(): string | null {
    if (!loaded) {
      token = read(STORAGE_KEY);
      loaded = true;
    }
    return token;
  },
  set(value: string | null): void {
    token = value;
    loaded = true;
    write(STORAGE_KEY, value);
    for (const l of listeners) l();
  },
  clear(): void {
    session.set(null);
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

/** Where to go back to after signing in again (a 401 mid-task). */
export const returnTo = {
  remember(href: string): void {
    write(RETURN_KEY, href);
  },
  take(): string | null {
    const v = read(RETURN_KEY);
    write(RETURN_KEY, null);
    return v;
  },
};

/** A consequential action interrupted by a provider step-up; shown as "retry" when the person comes back. */
export interface PendingAction {
  label: string;
  href: string;
}

export const pendingAction = {
  set(action: PendingAction): void {
    write(PENDING_KEY, JSON.stringify(action));
  },
  take(): PendingAction | null {
    const raw = read(PENDING_KEY);
    write(PENDING_KEY, null);
    if (!raw) return null;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && "label" in parsed && "href" in parsed) {
        const p = parsed as Record<string, unknown>;
        if (typeof p.label === "string" && typeof p.href === "string") return { label: p.label, href: p.href };
      }
    } catch {
      /* ignore */
    }
    return null;
  },
};

export type Fragment =
  | { kind: "session"; token: string }
  | { kind: "signin_error"; message: string }
  | { kind: "step_up" }
  | { kind: "link" }
  /** The previous interface's invitation links (`/#/accept/<token>`) keep working. */
  | { kind: "legacy_accept"; token: string };

/**
 * The identity-provider callback hands the session token (or an outcome) to the page in the URL fragment, which
 * browsers never send to a server. Read it once and remove it from the address bar and the history at once.
 */
export function consumeFragment(
  loc: Pick<Location, "hash" | "pathname" | "search"> = location,
  hist: Pick<History, "replaceState"> = history,
): Fragment | null {
  const hash = loc.hash;
  const outcome = /^#(session|signin_error|step_up|link)=(.*)$/.exec(hash);
  const invite = /^#\/accept\/([\w-]+)/.exec(hash);
  if (!outcome && !invite) return null;
  hist.replaceState(null, "", loc.pathname + loc.search);
  if (invite?.[1]) return { kind: "legacy_accept", token: invite[1] };
  const kind = outcome?.[1];
  const value = outcome?.[2] ?? "";
  switch (kind) {
    case "session":
      return { kind, token: value };
    case "signin_error":
      return { kind, message: safeDecode(value) };
    case "step_up":
      return { kind: "step_up" };
    default:
      return { kind: "link" };
  }
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value.replaceAll("+", " "));
  } catch {
    return value;
  }
}
