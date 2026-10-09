import { createFileRoute, Outlet, useNavigate } from "@tanstack/react-router";

import { useAuth } from "../auth/AuthProvider";
import { requireSignedIn } from "../auth/guards";
import { signInFlash } from "../auth/pendingStep";
import { AppShell } from "../shell/AppShell";

/** Everything behind sign-in shares this layout; its guard supplies `me` to every child route's context. */
export const Route = createFileRoute("/_app")({
  beforeLoad: ({ context, location }) => {
    const me = requireSignedIn(context.auth, location.href);
    return { me };
  },
  component: AppLayout,
});

function AppLayout() {
  const auth = useAuth();
  const navigate = useNavigate();
  const signOut = () => {
    void auth.signOut().then(() => {
      signInFlash.set("Signed out.");
      void navigate({ to: "/sign-in", search: {} });
    });
  };
  return (
    <AppShell onSignOut={signOut}>
      <Outlet />
    </AppShell>
  );
}
