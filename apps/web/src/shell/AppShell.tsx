import { Link, useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useAuth, useMe } from "../auth/AuthProvider";
import { isClient, isFirmStaff, isPlatformAdmin } from "../auth/can";
import { pendingAction as pendingStore, type PendingAction } from "../auth/session";
import { Button } from "../ui/Button";
import { firmLabel } from "./ContextBar";
import styles from "./AppShell.module.css";
import { NewVersion, Offline, PendingActionNotice } from "./Banners";

/** The one shell of the three workspaces: skip link, navigation for the role, banners, and a main region that
 * takes focus on every route change so keyboard and screen-reader users land on the new screen. */
export function AppShell({
  children,
  onSignOut,
  initialPendingAction = null,
}: {
  children: ReactNode;
  onSignOut: () => void;
  initialPendingAction?: PendingAction | null;
}) {
  const me = useMe();
  const auth = useAuth();
  const mainRef = useRef<HTMLElement>(null);
  // The resolved location changes once the new screen has rendered (route chunks load lazily), so the focus move
  // below sees the new DOM; the location itself changes while the old screen is still on view.
  const pathname = useRouterState({ select: (s) => s.resolvedLocation?.pathname ?? s.location.pathname });
  const firstRender = useRef(true);
  const [pending, setPending] = useState<PendingAction | null>(initialPendingAction ?? pendingStore.take());

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    // Focus the main region unless the person is already working inside it (a tab link that stays on screen).
    const main = mainRef.current;
    if (main && !main.contains(document.activeElement)) main.focus({ preventScroll: false });
  }, [pathname]);

  return (
    <div className={styles.app}>
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <aside className={styles.side}>
        <div className={styles.brand}>
          <div className={styles.mark} aria-hidden="true">
            V
          </div>
          <div>
            AgentLedger
            <small>{workspaceName(me)}</small>
          </div>
        </div>
        <nav aria-label="Primary">
          <ul className={styles.navList}>
            {navItems(me).map((item) => (
              <li key={item.to}>
                <Link
                  to={item.to}
                  params={item.params ?? {}}
                  className={styles.navItem}
                  activeProps={{ className: styles.navItemActive, "aria-current": "page" }}
                  activeOptions={{ exact: item.exact ?? false }}
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className={styles.account}>
          <div className={styles.accountName}>{me.name}</div>
          <div className={styles.accountMeta}>
            {me.email}
            <br />
            {me.base_role.replaceAll("_", " ")} · firm <span className="mono">{firmLabel(me)}</span>
            <br />
            <span data-testid="auth-method">{me.auth_method}</span>
            {auth.config.identity === "workos" ? " · provider sign-in" : ""}
          </div>
          <Button size="sm" onClick={onSignOut} className={styles.signOut}>
            Sign out
          </Button>
        </div>
      </aside>
      <div className={styles.content}>
        <Offline />
        <NewVersion />
        {pending ? <PendingActionNotice action={pending} onDismiss={() => setPending(null)} /> : null}
        <main id="main" ref={mainRef} tabIndex={-1} className={styles.main}>
          {children}
        </main>
      </div>
    </div>
  );
}

function workspaceName(me: Parameters<typeof navItems>[0]): string {
  if (isPlatformAdmin(me)) return "Platform console";
  if (isClient(me)) return "Client portal";
  return "Practice";
}

interface NavItem {
  to: "/clients" | "/inbox" | "/team" | "/platform/firms" | "/portal" | "/clients/$clientId/documents";
  params?: Record<string, string>;
  label: string;
  exact?: boolean;
}

export function navItems(me: ReturnType<typeof useMe>): NavItem[] {
  if (isPlatformAdmin(me)) return [{ to: "/platform/firms", label: "Firms" }];
  if (isClient(me)) {
    const items: NavItem[] = [{ to: "/portal", label: "My business", exact: true }];
    if (me.client_id)
      items.push({ to: "/clients/$clientId/documents", params: { clientId: me.client_id }, label: "Documents" });
    return items;
  }
  if (isFirmStaff(me)) {
    return [
      { to: "/clients", label: "Clients" },
      { to: "/inbox", label: "Inbox" },
      { to: "/team", label: "Team" },
    ];
  }
  return [];
}
