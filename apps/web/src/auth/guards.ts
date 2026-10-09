/**
 * Route guards for `beforeLoad`. A signed-out person is sent to sign-in with where they were; a role the API would
 * refuse gets the Forbidden state with the API's text, before any client-scoped query is issued (platform
 * administrators never trigger client reads: the API answers 403 to those).
 */

import type { Me } from "@agentledger/contracts";
import { redirect } from "@tanstack/react-router";

import { RouteForbidden } from "../ui/states/mapError";
import type { AuthSnapshot } from "./AuthProvider";
import { REASONS, engaged, isClient, isFirmStaff, isPlatformAdmin } from "./can";

export function requireSignedIn(auth: AuthSnapshot, href: string): Me {
  if (!auth.me) {
    throw redirect({ to: "/sign-in", search: { next: href } });
  }
  return auth.me;
}

export function requireFirmStaff(me: Me): void {
  if (isPlatformAdmin(me)) throw new RouteForbidden(REASONS.platformNoClientData);
  if (!isFirmStaff(me)) throw new RouteForbidden(REASONS.cpaOnly);
}

export function requireClientAccess(me: Me, clientId: string): void {
  if (isPlatformAdmin(me)) throw new RouteForbidden(REASONS.platformNoClientData);
  if (!engaged(me, clientId)) throw new RouteForbidden(isClient(me) ? REASONS.ownBusinessOnly : REASONS.notEngaged);
}

export function requirePlatformAdmin(me: Me): void {
  if (!isPlatformAdmin(me)) throw new RouteForbidden(REASONS.platformAdminOnly);
}

export function requireClient(me: Me): void {
  if (!isClient(me)) throw new RouteForbidden(REASONS.ownBusinessOnly);
}
