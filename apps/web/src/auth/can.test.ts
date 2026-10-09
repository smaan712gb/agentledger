import { describe, expect, it } from "vitest";

import { clientUser, cpa, firmAdmin, platformAdmin, staff } from "../test/msw/fixtures";
import { REASONS, can, engaged, homeFor, isClient, isFirmStaff, isPlatformAdmin, isReviewer } from "./can";

describe("role predicates mirror app.py", () => {
  it("classifies the five base roles", () => {
    expect(isPlatformAdmin(platformAdmin)).toBe(true);
    expect(isFirmStaff(firmAdmin)).toBe(true);
    expect(isFirmStaff(cpa)).toBe(true);
    expect(isFirmStaff(staff)).toBe(true);
    expect(isFirmStaff(clientUser)).toBe(false);
    expect(isClient(clientUser)).toBe(true);
  });

  it("reviewer authority: CPAs always, firm administrators only when recorded", () => {
    expect(isReviewer(cpa)).toBe(true);
    expect(isReviewer(firmAdmin)).toBe(false);
    expect(isReviewer({ ...firmAdmin, reviewer: true })).toBe(true);
    expect(isReviewer(staff)).toBe(false);
  });

  it("engagement follows scope(): clients their own business, staff their grants, others the firm", () => {
    expect(engaged(clientUser, "ortiz-auto")).toBe(true);
    expect(engaged(clientUser, "lakeside-fuel")).toBe(false);
    expect(engaged(staff, "ortiz-auto")).toBe(true);
    expect(engaged(staff, "lakeside-fuel")).toBe(false);
    expect(engaged(firmAdmin, "lakeside-fuel")).toBe(true);
    expect(engaged(platformAdmin, "ortiz-auto")).toBe(false);
  });

  it("sends each role to its own landing", () => {
    expect(homeFor(platformAdmin)).toBe("/platform/firms");
    expect(homeFor(clientUser)).toBe("/portal");
    expect(homeFor(staff)).toBe("/clients");
  });
});

describe("can() quotes the API's reasons", () => {
  it("platform administrators never read client data", () => {
    expect(can(platformAdmin, "clients.list")).toEqual({ allowed: false, reason: REASONS.platformNoClientData });
    expect(can(platformAdmin, "clients.view", { clientId: "ortiz-auto" }).reason).toBe(REASONS.platformNoClientData);
    expect(can(platformAdmin, "firms.create")).toEqual({ allowed: true });
  });

  it("firm staff share the CPA screens; clients do not", () => {
    expect(can(staff, "clients.create")).toEqual({ allowed: true });
    expect(can(clientUser, "clients.create")).toEqual({ allowed: false, reason: REASONS.cpaOnly });
    expect(can(clientUser, "inbox.view").reason).toBe(REASONS.cpaOnly);
    expect(can(firmAdmin, "firms.list").reason).toBe(REASONS.platformAdminOnly);
  });

  it("client scope: own business only, staff only when engaged", () => {
    expect(can(clientUser, "clients.view", { clientId: "ortiz-auto" })).toEqual({ allowed: true });
    expect(can(clientUser, "clients.view", { clientId: "lakeside-fuel" }).reason).toBe(REASONS.ownBusinessOnly);
    expect(can(staff, "documents.upload", { clientId: "lakeside-fuel" }).reason).toBe(REASONS.notEngaged);
  });

  it("invitations: CPAs invite clients only, staff none, administrators anyone", () => {
    expect(can(cpa, "users.invite", { role: "client" })).toEqual({ allowed: true });
    expect(can(cpa, "users.invite", { role: "staff" }).reason).toBe(REASONS.staffInvitesNeedAdmin);
    expect(can(staff, "users.invite", { role: "client" }).reason).toBe(REASONS.cannotInvite);
    expect(can(firmAdmin, "users.invite", { role: "firm_admin" })).toEqual({ allowed: true });
  });

  it("disabling accounts is a firm administrator's action", () => {
    expect(can(firmAdmin, "users.disable")).toEqual({ allowed: true });
    expect(can(cpa, "users.disable")).toMatchObject({ allowed: false, reason: REASONS.notAllowed });
  });
});
