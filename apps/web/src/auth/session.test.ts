import { describe, expect, it, vi } from "vitest";

import { consumeFragment, pendingAction, session } from "./session";

function fakeLocation(hash: string) {
  const hist = { replaceState: vi.fn() };
  const loc = { hash, pathname: "/clients", search: "?q=x" };
  return { loc, hist };
}

describe("consumeFragment", () => {
  it("returns null and touches nothing without a known fragment", () => {
    const { loc, hist } = fakeLocation("");
    expect(consumeFragment(loc, hist)).toBeNull();
    expect(hist.replaceState).not.toHaveBeenCalled();
  });

  it("takes the session token the provider callback left and strips the fragment at once", () => {
    const { loc, hist } = fakeLocation("#session=abc.def");
    expect(consumeFragment(loc, hist)).toEqual({ kind: "session", token: "abc.def" });
    expect(hist.replaceState).toHaveBeenCalledWith(null, "", "/clients?q=x");
  });

  it("decodes sign-in errors", () => {
    const { loc, hist } = fakeLocation("#signin_error=this%20invitation%20is%20invalid");
    expect(consumeFragment(loc, hist)).toEqual({ kind: "signin_error", message: "this invitation is invalid" });
  });

  it("recognises step-up and link outcomes", () => {
    expect(consumeFragment(fakeLocation("#step_up=ok").loc, fakeLocation("").hist)).toEqual({ kind: "step_up" });
    expect(consumeFragment(fakeLocation("#link=ok").loc, fakeLocation("").hist)).toEqual({ kind: "link" });
  });

  it("keeps the previous interface's invitation links working", () => {
    const { loc, hist } = fakeLocation("#/accept/tok_ABC-123");
    expect(consumeFragment(loc, hist)).toEqual({ kind: "legacy_accept", token: "tok_ABC-123" });
    expect(hist.replaceState).toHaveBeenCalled();
  });
});

describe("session storage", () => {
  it("keeps the token in sessionStorage, never localStorage", () => {
    session.set("tok");
    expect(sessionStorage.getItem("agentledger.session")).toBe("tok");
    expect(localStorage.getItem("agentledger.session")).toBeNull();
    session.clear();
    expect(sessionStorage.getItem("agentledger.session")).toBeNull();
    expect(session.get()).toBeNull();
  });

  it("notifies subscribers", () => {
    const seen: (string | null)[] = [];
    const off = session.subscribe(() => seen.push(session.get()));
    session.set("a");
    session.clear();
    off();
    session.set("b");
    expect(seen).toEqual(["a", null]);
    session.clear();
  });

  it("remembers a pending action once", () => {
    pendingAction.set({ label: "creating the firm", href: "/platform/firms" });
    expect(pendingAction.take()).toEqual({ label: "creating the firm", href: "/platform/firms" });
    expect(pendingAction.take()).toBeNull();
  });
});
