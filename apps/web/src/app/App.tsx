import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { AuthProvider, identityKey, useAuth, type AuthStore } from "../auth/AuthProvider";
import { sessionEnded } from "../auth/bridges";
import { IdleWarning } from "../auth/IdleWarning";
import { signInFlash } from "../auth/pendingStep";
import { returnTo } from "../auth/session";
import { StepUpProvider } from "../auth/StepUpProvider";
import { ToastProvider, useToast } from "../ui/Toast";
import { TooltipProvider } from "../ui/Tooltip";
import { bootNotices } from "./bootState";
import type { AppRouter } from "./router";

/** `store` is the same object the router was created with (RouterContext.auth). */
export function App({ store, router }: { store: AuthStore; router: AppRouter }) {
  return (
    <QueryClientProvider client={router.options.context.queryClient}>
      <TooltipProvider>
        <ToastProvider>
          <AuthProvider store={store}>
            <StepUpProvider>
              <Inner router={router} />
            </StepUpProvider>
          </AuthProvider>
        </ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

function Inner({ router }: { router: AppRouter }) {
  const auth = useAuth();
  const { toast } = useToast();
  const identity = identityKey(auth.me);

  // The session rule: a 401 anywhere but the sign-in steps forgets the session, remembers the place, and goes to sign-in.
  useEffect(
    () =>
      sessionEnded.subscribe(() => {
        const here = location.pathname + location.search;
        returnTo.remember(here);
        auth.endSession();
        signInFlash.set("Your session ended. Sign in again.");
        void router.navigate({ to: "/sign-in", search: { next: here } });
      }),
    [router, auth],
  );

  // Guards read the shared store; when the person, role or engagements change they must run again.
  const lastIdentity = useRef(identity);
  useEffect(() => {
    if (lastIdentity.current === identity) return;
    lastIdentity.current = identity;
    void router.invalidate();
  }, [router, identity]);

  useEffect(() => {
    const notices = bootNotices.take();
    if (notices.toast) toast(notices.toast, "success");
  }, [toast]);

  const signOut = () => {
    void auth.signOut().then(() => {
      signInFlash.set("Signed out.");
      void router.navigate({ to: "/sign-in", search: {} });
    });
  };

  return (
    <>
      <RouterProvider router={router} />
      <IdleWarning onSignOut={signOut} />
    </>
  );
}
