/** The schema -> form mapping the editor is generated from, against the exported IndividualReturn schema itself. */
import returnSchema from "@agentledger/contracts/return-schema.json";
import { describe, expect, it } from "vitest";

import { SECTIONS, boxLabel, fieldLabel, planSections } from "./labels";
import { anchorFor, getPath, listOf, locToPath, locateAnchor, parsePath, setPath } from "./paths";
import { missingAmounts, neverZero, satisfied, validCode } from "./rules";
import { compileSchema, displayValue, kindOf, normaliseMoney, rootFieldNames, type JsonSchema } from "./schema";

const document: JsonSchema = returnSchema;
const schema = compileSchema(document);
const field = (model: string, name: string) => {
  const spec = (model === "IndividualReturn" ? schema.root : schema.models[model])?.fields.find((f) => f.name === name);
  if (!spec) throw new Error(`${model}.${name} is not in the schema`);
  return spec;
};

describe("the editor model follows the pydantic schema", () => {
  it("tells an Optional amount (not stated) from a defaulted one (0)", () => {
    const tips = field("W2", "qualified_tips");
    expect(tips.kind).toEqual({ kind: "money" });
    expect(tips.nullable).toBe(true);
    expect(tips.default).toBeNull();
    const wages = field("W2", "wages");
    expect(wages.kind).toEqual({ kind: "money" });
    expect(wages.nullable).toBe(false);
    expect(wages.default).toBe("0");
    expect(field("IRAAccount", "fmv").nullable).toBe(true); // "amounts default to None, not zero"
  });

  it("maps lists of models, nested groups, maps, dates, choices and tri-state booleans", () => {
    expect(field("IndividualReturn", "w2s").kind).toEqual({ kind: "list", item: { kind: "object", model: "W2" } });
    expect(field("IndividualReturn", "taxpayer").kind).toEqual({ kind: "object", model: "Person" });
    const spouse = field("IndividualReturn", "spouse");
    expect(spouse.kind).toEqual({ kind: "object", model: "Person" });
    expect(spouse.nullable).toBe(true);
    expect(field("W2", "box12").kind).toEqual({ kind: "map", value: { kind: "money" }, keys: null });
    expect(field("IndividualReturn", "retirement_savings_contributions").kind).toEqual({
      kind: "map",
      value: { kind: "money" },
      keys: ["taxpayer", "spouse"],
    });
    expect(field("Person", "dob")).toMatchObject({ kind: { kind: "date" }, nullable: true });
    expect(field("Dependent", "dob")).toMatchObject({ kind: { kind: "date" }, nullable: false, required: true });
    expect(field("IndividualReturn", "filing_status").kind).toEqual({
      kind: "enum",
      options: ["single", "mfj", "mfs", "hoh", "qss"],
    });
    expect(field("Person", "full_time_student")).toMatchObject({ kind: { kind: "boolean" }, nullable: true });
    expect(field("Person", "blind")).toMatchObject({ kind: { kind: "boolean" }, nullable: false });
    expect(field("CapitalTransaction", "acquired").kind).toEqual({
      kind: "text-or-enum",
      options: ["various", "inherited"],
    });
    expect(field("Dependent", "months_in_home").kind).toEqual({ kind: "integer", min: 0, max: 12 });
    expect(field("MarketplaceCoverage", "covered_individuals").kind).toEqual({ kind: "list", item: { kind: "text" } });
  });

  it("marks the model's required fields", () => {
    expect(field("IndividualReturn", "tax_year").required).toBe(true);
    expect(field("Dependent", "first_name").required).toBe(true);
    expect(field("W2", "wages").required).toBe(false);
  });

  it("places every root field in a section, so a field the model gains is never hidden", () => {
    const names = rootFieldNames(schema);
    const sections = planSections(names);
    const placed = sections.flatMap((s) => s.fields);
    expect([...placed].sort()).toEqual([...names].sort());
    expect(new Set(placed).size).toBe(placed.length);
    expect(planSections([...names, "brand_new_input"]).find((s) => s.id === "other")?.fields).toContain(
      "brand_new_input",
    );

    for (const s of SECTIONS) for (const f of s.fields) expect(names, `${f} left the model`).toContain(f);
  });

  it("reads bare nodes too", () => {
    expect(kindOf({ anyOf: [{ type: "number" }, { type: "string" }, { type: "null" }] })).toEqual({
      kind: { kind: "money" },
      nullable: true,
    });
    expect(kindOf({ $ref: "#/$defs/W2" })).toEqual({ kind: { kind: "object", model: "W2" }, nullable: false });
    expect(kindOf({})).toEqual({ kind: { kind: "text" }, nullable: false });
  });
});

describe("amounts are decimal strings, never floats", () => {
  it("normalises what is typed and refuses what is not a number", () => {
    expect(normaliseMoney("61,200.00")).toEqual({ value: "61200.00", valid: true });
    expect(normaliseMoney("$ 1 234.5")).toEqual({ value: "1234.5", valid: true });
    expect(normaliseMoney("(500)")).toEqual({ value: "-500", valid: true });
    expect(normaliseMoney("")).toEqual({ value: null, valid: true });
    expect(normaliseMoney("twelve")).toEqual({ value: "twelve", valid: false });
    expect(normaliseMoney("1.2.3")).toEqual({ value: "1.2.3", valid: false });
  });

  it("shows stored values as text", () => {
    expect(displayValue("61200.00")).toBe("61200.00");
    expect(displayValue(60000)).toBe("60000");
    expect(displayValue(null)).toBe("");
    expect(displayValue(undefined)).toBe("");
  });
});

describe("paths and anchors", () => {
  const inputs = {
    w2s: [{ source_document: "doc_a", wages: "1" }, { wages: "2" }],
    taxpayer: { ssn: "400-00-0009" },
    other_income: { "8a": "10" },
  };

  it("parses, reads and writes positional paths immutably", () => {
    expect(parsePath("w2s[0].box12.D")).toEqual(["w2s", 0, "box12", "D"]);
    expect(getPath(inputs, "w2s[1].wages")).toBe("2");
    expect(getPath(inputs, "other_income.8a")).toBe("10");
    const next = setPath(inputs, "w2s[0].wages", "3");
    expect(getPath(next, "w2s[0].wages")).toBe("3");
    expect(getPath(inputs, "w2s[0].wages")).toBe("1");
    expect(getPath(setPath(inputs, "w2s[1]", undefined), "w2s")).toEqual([{ source_document: "doc_a", wages: "1" }]);
    expect(getPath(setPath(inputs, "taxpayer.ssn", undefined), "taxpayer")).toEqual({});
    expect(getPath(setPath(inputs, "spouse.first_name", "Sam"), "spouse")).toEqual({ first_name: "Sam" });
    expect(getPath(setPath(inputs, "w2s[2].wages", "4"), "w2s[2]")).toEqual({ wages: "4" });
  });

  it("names list items by their document, hand items by position", () => {
    expect(anchorFor("w2s[0].wages", inputs)).toBe("w2s[doc_a].wages");
    expect(anchorFor("w2s[1].wages", inputs)).toBe("w2s[#1].wages");
    expect(anchorFor("taxpayer.ssn", inputs)).toBe("taxpayer.ssn");
    expect(locateAnchor("w2s[doc_a].wages", inputs)).toBe("w2s[0].wages");
    expect(locateAnchor("missing:w2s[doc_a].wages", inputs)).toBe("w2s[0].wages");
    expect(locateAnchor("w2s[doc_zz].wages", inputs)).toBeNull();
    expect(locateAnchor("orphan:w2s[doc_a]", inputs)).toBe("w2s[0]");
    expect(listOf("w2s[0].wages")).toBe("w2s");
    expect(listOf("taxpayer.ssn")).toBeNull();
    expect(locToPath(["body", "w2s", 0, "wages"])).toBe("w2s[0].wages");
  });
});

describe("the never-zero rule (facts.py REQUIRED, IMPLIED, CODED)", () => {
  it("knows which blanks are missing amounts", () => {
    expect(neverZero("w2s", "wages")).toEqual({ kind: "required" });
    expect(neverZero("w2s", "medicare_wages")).toEqual({ kind: "implied", by: "medicare_tax" });
    expect(neverZero("retirement", "distribution_code")).toEqual({ kind: "code" });
    expect(neverZero("w2s", "ss_tips")).toBeNull();
    expect(neverZero(null, "wages")).toBeNull();
  });

  it("lists missing amounts like facts.missing_required", () => {
    const missing = missingAmounts({
      w2s: [
        { source_document: "doc_a", medicare_tax: "10" },
        { wages: "100", ss_wages: "0", ss_tax: "6.2" },
      ],
      retirement: [{ gross_distribution: "500", distribution_code: "x" }],
    });
    expect(missing.map((m) => m.anchor)).toEqual([
      "missing:w2s[doc_a].wages",
      "missing:w2s[doc_a].medicare_wages",
      "missing:w2s[#1].ss_wages",
      "missing:retirement[#0].distribution_code",
    ]);
    expect(validCode("7", "retirement")).toBe(true);
    expect(validCode("7D", "retirement")).toBe(true);
    expect(validCode("x", "retirement")).toBe(false);
    expect(validCode("7", "hsa_distributions")).toBe(false);
    expect(satisfied({ w2s: [{ wages: "0" }] }, "w2s", "wages", "w2s[0].wages")).toBe(true);
    expect(satisfied({ w2s: [{ medicare_wages: "0" }] }, "w2s", "medicare_wages", "w2s[0].medicare_wages")).toBe(false);
  });
});

describe("labels", () => {
  it("names boxes per list and falls back to the identifier", () => {
    expect(boxLabel("w2s", "box1")).toBe("Box 1 — Wages, tips, other compensation");
    expect(boxLabel("w2s", "box12 D")).toBe("Box 12 — code D");
    expect(boxLabel("interest", "box99")).toBe("Box 99");
    expect(boxLabel(null, null)).toBe("the document");
    expect(fieldLabel("W2", "wages")).toBe("Wages, tips, other compensation (box 1)");
    expect(fieldLabel("W2", "some_new_box")).toBe("Some new box");
  });
});
