import { describe, expect, it } from "vitest";

import { describeUpload } from "./DocumentsScreen";

describe("describeUpload: one line per uploaded part", () => {
  it("reports a filed document as done", () => {
    const [item] = describeUpload("1099.txt", [
      {
        id: "d1",
        client_id: "c",
        status: "filed",
        doc_type: "1099-INT",
        tax_year: 2025,
        confidence: 0.8,
        vault_path: "blob:x",
        retention_class: "tax",
        match: "explicit",
        name: "1099.txt",
        suggested_client: null,
      },
    ]);
    expect(item).toMatchObject({ outcome: "ok", label: "1099.txt" });
    expect(item?.detail).toContain("filed as 1099-INT 2025");
  });

  it("reports a document the API could not file as needing attention, not as a failure", () => {
    const [item] = describeUpload("scan.bin", [
      {
        id: "d2",
        client_id: null,
        status: "needs_review",
        doc_type: "Other",
        tax_year: null,
        confidence: 0,
        vault_path: "blob:y",
        retention_class: null,
        match: "",
        name: "scan.bin",
        suggested_client: "c",
      },
    ]);
    expect(item).toMatchObject({ outcome: "warn" });
    expect(item?.detail).toContain("waiting in the inbox");
  });

  it("flags duplicates and empty results", () => {
    expect(
      describeUpload("again.pdf", [
        { duplicate: true, id: "d1", client_id: "c", status: "filed", vault_path: "blob:x", name: "again.pdf" },
      ])[0],
    ).toMatchObject({ outcome: "warn" });
    expect(describeUpload("empty.zip", [])[0]).toMatchObject({ outcome: "warn", label: "empty.zip" });
  });
});
