/**
 * Who may do what, mirroring src/agentledger/api/app.py (`cpa_only`, `platform_admin`, `scope`, `authority`) and
 * security/platform.py (`invite`, `set_disabled`). The API stays the enforcement point: these predicates decide
 * what navigation to show and which buttons to disable (never hide) with the API's own reason as the tooltip, so a
 * person always learns why, and a 403 from the API is still rendered when the two disagree.
 */

import type { BaseRole, Me } from "@agentledger/contracts";

export const FIRM_ROLES: readonly BaseRole[] = ["firm_admin", "cpa", "staff"];

export function isPlatformAdmin(me: Me): boolean {
  return me.base_role === "platform_admin";
}

export function isFirmStaff(me: Me): boolean {
  return FIRM_ROLES.includes(me.base_role);
}

/** Holds authority over returns, risks, periods and filings: CPAs, and firm administrators recorded as reviewers. */
export function isReviewer(me: Me): boolean {
  return me.base_role === "cpa" || (me.base_role === "firm_admin" && me.reviewer);
}

export function isClient(me: Me): boolean {
  return me.base_role === "client";
}

/** May this person open this client's workspace? (app.py `scope`) */
export function engaged(me: Me, clientId: string): boolean {
  if (isClient(me)) return me.client_id === clientId;
  if (me.base_role === "staff") return (me.engaged ?? []).includes(clientId);
  return isFirmStaff(me);
}

// The API's own messages, quoted so the tooltip says what the response would.
export const REASONS = {
  cpaOnly: "CPA access required",
  platformAdminOnly: "platform administrator access required",
  platformNoClientData: "platform administrators manage firms; client data is only reachable from inside a firm",
  ownBusinessOnly: "you can only access your own business",
  notEngaged: "you are not engaged on this client; ask a CPA or your firm administrator",
  staffInvitesNeedAdmin: "only a firm administrator can invite staff",
  cannotInvite: "you cannot invite users to this firm",
  notAllowed: "not allowed",
  reviewerOnly: "this action needs a credentialed reviewer (CPA), not firm staff or an administrator",
} as const;

export type Action =
  | "clients.list"
  | "clients.create"
  | "clients.view"
  | "facts.update"
  | "documents.upload"
  | "documents.assign"
  | "inbox.view"
  | "users.list"
  | "users.invite"
  | "users.disable"
  | "firms.list"
  | "firms.create";

export interface Decision {
  allowed: boolean;
  /** The API's reason, when not allowed. */
  reason?: string;
  /** A plain-language hint to show next to the reason. */
  hint?: string;
}

const ALLOW: Decision = { allowed: true };

function deny(reason: string, hint?: string): Decision {
  return hint ? { allowed: false, reason, hint } : { allowed: false, reason };
}

export interface ActionContext {
  clientId?: string;
  /** For users.invite: the role being invited. */
  role?: BaseRole;
}

export function can(me: Me, action: Action, ctx: ActionContext = {}): Decision {
  switch (action) {
    case "clients.list":
      return isPlatformAdmin(me) ? deny(REASONS.platformNoClientData) : ALLOW;
    case "clients.create":
      if (isPlatformAdmin(me)) return deny(REASONS.platformNoClientData);
      return isFirmStaff(me) ? ALLOW : deny(REASONS.cpaOnly);
    case "clients.view":
    case "facts.update":
    case "documents.upload": {
      if (isPlatformAdmin(me)) return deny(REASONS.platformNoClientData);
      if (!ctx.clientId) return ALLOW;
      if (engaged(me, ctx.clientId)) return ALLOW;
      return deny(isClient(me) ? REASONS.ownBusinessOnly : REASONS.notEngaged);
    }
    case "inbox.view":
    case "documents.assign":
    case "users.list":
      if (isPlatformAdmin(me)) return deny(REASONS.platformNoClientData);
      return isFirmStaff(me) ? ALLOW : deny(REASONS.cpaOnly);
    case "users.invite": {
      if (isPlatformAdmin(me)) return ALLOW;
      if (!isFirmStaff(me)) return deny(REASONS.cannotInvite);
      const role = ctx.role;
      if (me.base_role === "firm_admin") return ALLOW;
      if (me.base_role === "cpa") {
        return role && role !== "client" ? deny(REASONS.staffInvitesNeedAdmin) : ALLOW;
      }
      return deny(REASONS.cannotInvite, "staff do not invite; ask a CPA or your firm administrator");
    }
    case "users.disable":
      return me.base_role === "firm_admin" || isPlatformAdmin(me)
        ? ALLOW
        : deny(REASONS.notAllowed, "firm administrators enable and disable accounts");
    case "firms.list":
    case "firms.create":
      return isPlatformAdmin(me) ? ALLOW : deny(REASONS.platformAdminOnly);
  }
}

/** The landing route for a role (app.js `route()` default view). */
export function homeFor(me: Me): "/platform/firms" | "/portal" | "/clients" {
  if (isPlatformAdmin(me)) return "/platform/firms";
  if (isClient(me)) return "/portal";
  return "/clients";
}
