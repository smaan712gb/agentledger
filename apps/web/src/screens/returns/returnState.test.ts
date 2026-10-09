import { describe, expect, it } from "vitest";

import * as fx from "../../test/msw/fixtures";
import {
  availableActions,
  blockers,
  editability,
  reliedOn,
  splitReasons,
  submittedBy,
  unaccountedDocuments,
} from "./returnState";

describe("editability follows store.py's status sets", () => {
  it("preparing is editable; review and hash-bound statuses reopen on save; filed statuses are frozen", () => {
    expect(editability("preparing")).toEqual({ mode: "editable" });
    expect(editability("in_review").mode).toBe("bound");
    for (const s of ["approved", "awaiting_signature", "signed", "release_approved"] as const) {
      const e = editability(s);
      expect(e.mode).toBe("bound");
      if (e.mode === "bound") expect(e.consequence).toMatch(/reopens/);
    }
    for (const s of ["transmitted", "accepted", "paper_filed", "rejected", "void", "unknown"] as const) {
      expect(editability(s).mode).toBe("frozen");
    }
    const accepted = editability("accepted");
    if (accepted.mode === "frozen") expect(accepted.path).toMatch(/amendment/);
    const unknown = editability("unknown");
    if (unknown.mode === "frozen") expect(unknown.path).toMatch(/Reconcile/);
  });
});

describe("segregation of duties", () => {
  it("finds the submitter in the history", () => {
    expect(submittedBy(fx.returnDetail.history)).toBeNull();
    expect(submittedBy(fx.returnWith("in_review", "u_cpa").history)).toBe("u_cpa");
    expect(submittedBy(fx.returnWith("approved").history)).toBe("u_admin");
  });
});

describe("the blocking checklist mirrors store.py _blockers", () => {
  it("lists what the app can see, with stable codes", () => {
    const list = blockers(fx.returnDetail, fx.conflicts, [fx.docNec]);
    const codes = list.map((b) => b.code);
    expect(codes).toEqual(["missing_amount", "unconfirmed", "fact_conflict", "unaccounted_document"]);
    expect(list.find((b) => b.code === "missing_amount")?.text).toMatch(/never taken as zero/);
    expect(list.find((b) => b.code === "unconfirmed")?.count).toBe(6);
    expect(list.find((b) => b.code === "unaccounted_document")?.text).toContain("doc_nec");
  });

  it("names engine diagnostics by their code and asks for a computation first", () => {
    const list = blockers(
      {
        ...fx.freshReturn,
        result: null,
      },
      [],
      [],
    );
    expect(list[0]).toMatchObject({ code: "not_computed" });
    const withError = blockers(
      {
        ...fx.returnDetail,
        provenance: {},
        inputs: { ...fx.returnInputs, w2s: [] },
        result: {
          ...fx.returnResult,
          diagnostics: [
            { severity: "error", code: "taxpayer_ssn_missing", message: "SSN missing", form: null, line: null },
          ],
        },
        crosscheck: { status: "differ", discrepancies: [{ item: "agi", agentledger: "1", policyengine: "2" }] },
      },
      [],
      [],
    );
    expect(withError.map((b) => b.code)).toEqual(["taxpayer_ssn_missing", "crosscheck_differs"]);
  });
});

describe("documents and the return", () => {
  it("knows which documents the return relies on, including the ones a preparer's edit replaced", () => {
    const used = reliedOn(fx.returnInputs, fx.returnProvenance);
    expect([...used].sort()).toEqual(["doc_w2a", "doc_w2b"]);
  });

  it("finds the filed tax forms of the year the return neither uses nor accounted for", () => {
    expect(unaccountedDocuments(fx.returnDetail, fx.ortizDocuments, []).map((d) => d.id)).toEqual(["doc_nec"]);
    expect(unaccountedDocuments(fx.returnDetail, fx.ortizDocuments, [fx.dispositionFor("doc_nec")])).toEqual([]);
  });
});

describe("actions", () => {
  it("offers what the status allows, and the amendment only for filed returns", () => {
    expect(availableActions(fx.returnDetail).map((a) => a.id)).toEqual(["submit", "void"]);
    expect(availableActions(fx.returnWith("in_review")).map((a) => a.id)).toEqual([
      "approve",
      "request-changes",
      "void",
    ]);
    expect(availableActions(fx.returnWith("accepted")).map((a) => a.id)).toEqual(["amend"]);
    expect(availableActions(fx.returnWith("unknown")).map((a) => a.id)).toEqual(["reconcile"]);
  });

  it("splits the workflow's refusal into its reasons", () => {
    expect(
      splitReasons("compute the return first; 2 fact conflict(s) between documents and the return must be resolved"),
    ).toEqual(["compute the return first", "2 fact conflict(s) between documents and the return must be resolved"]);
    expect(splitReasons(undefined)).toEqual([]);
  });
});
