/**
 * The inputs editor is generated from the JSON schema of `IndividualReturn` (packages/contracts/src/return-schema.json,
 * written from the pydantic model by `npm run -w packages/contracts return-schema`), so a field the model gains appears
 * in the editor without a code change here. This module reads that schema into a small field model the editor renders
 * from: what each field is (money, a date, a choice, a nested group, a list of items, a keyed map), whether the model
 * lets it be null (unknown, "not stated", which the engine never reads as zero), and whether it has a default.
 */

export interface JsonSchema {
  type?: string;
  anyOf?: JsonSchema[];
  $ref?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  additionalProperties?: JsonSchema | boolean;
  propertyNames?: JsonSchema;
  enum?: unknown[];
  default?: unknown;
  format?: string;
  pattern?: string;
  minimum?: number;
  maximum?: number;
  title?: string;
  description?: string;
  $defs?: Record<string, JsonSchema>;
}

export type FieldKind =
  /** A Decimal: pydantic emits `anyOf [number, string]`; the app always sends a decimal string. */
  | { kind: "money" }
  | { kind: "integer"; min?: number; max?: number }
  | { kind: "text" }
  | { kind: "date" }
  | { kind: "boolean" }
  | { kind: "enum"; options: string[] }
  /** `date | Literal[...]` (CapitalTransaction.acquired): a date, or one of the words. */
  | { kind: "text-or-enum"; options: string[] }
  /** A nested model (`$ref`), rendered as a group. */
  | { kind: "object"; model: string }
  | { kind: "list"; item: FieldKind }
  /** `dict[str, Money]` (box 12 codes, Schedule 1 line 8 items); `keys` when the key is a Literal. */
  | { kind: "map"; value: FieldKind; keys: string[] | null };

export interface FieldSpec {
  name: string;
  kind: FieldKind;
  /** The model allows null: "not stated", distinct from zero. */
  nullable: boolean;
  /** No default: the model refuses the item without it. */
  required: boolean;
  default: unknown;
}

export interface ModelSpec {
  name: string;
  fields: FieldSpec[];
}

export interface ReturnSchema {
  root: ModelSpec;
  models: Record<string, ModelSpec>;
}

function refName(ref: string): string {
  return ref.slice(ref.lastIndexOf("/") + 1);
}

function strings(values: unknown[] | undefined): string[] {
  return (values ?? []).map((v) => String(v));
}

/** What one schema node is, with its nullability (`anyOf [..., {type: null}]`). */
export function kindOf(node: JsonSchema): { kind: FieldKind; nullable: boolean } {
  if (node.$ref) return { kind: { kind: "object", model: refName(node.$ref) }, nullable: false };
  if (node.anyOf) {
    const variants = node.anyOf.filter((v) => v.type !== "null");
    const nullable = variants.length !== node.anyOf.length;
    const [single] = variants;
    if (variants.length === 1 && single) return { kind: kindOf(single).kind, nullable };
    const types = new Set(variants.map((v) => v.type));
    const hasEnum = variants.some((v) => v.enum !== undefined);
    if (variants.length === 2 && types.has("number") && types.has("string") && !hasEnum) {
      return { kind: { kind: "money" }, nullable };
    }
    const words = variants.find((v) => v.enum !== undefined);
    if (words && variants.every((v) => v.type === "string")) {
      return { kind: { kind: "text-or-enum", options: strings(words.enum) }, nullable };
    }
    return { kind: { kind: "text" }, nullable }; // anything else is typed in as text; the API validates it
  }
  if (node.enum !== undefined) return { kind: { kind: "enum", options: strings(node.enum) }, nullable: false };
  switch (node.type) {
    case "integer": {
      const kind: FieldKind = { kind: "integer" };
      if (node.minimum !== undefined) kind.min = node.minimum;
      if (node.maximum !== undefined) kind.max = node.maximum;
      return { kind, nullable: false };
    }
    case "number":
      return { kind: { kind: "money" }, nullable: false };
    case "boolean":
      return { kind: { kind: "boolean" }, nullable: false };
    case "string":
      return { kind: node.format === "date" ? { kind: "date" } : { kind: "text" }, nullable: false };
    case "array":
      return { kind: { kind: "list", item: kindOf(node.items ?? { type: "string" }).kind }, nullable: false };
    case "object": {
      const value = typeof node.additionalProperties === "object" ? node.additionalProperties : { type: "string" };
      const keys = node.propertyNames?.enum;
      return { kind: { kind: "map", value: kindOf(value).kind, keys: keys ? strings(keys) : null }, nullable: false };
    }
    default:
      return { kind: { kind: "text" }, nullable: false };
  }
}

function modelOf(name: string, node: JsonSchema): ModelSpec {
  const required = new Set(node.required ?? []);
  const fields = Object.entries(node.properties ?? {}).map(([field, prop]): FieldSpec => {
    const { kind, nullable } = kindOf(prop);
    return { name: field, kind, nullable, required: required.has(field), default: prop.default };
  });
  return { name, fields };
}

/** The whole schema: the root model and every `$defs` model, in the field order the model declares. */
export function compileSchema(schema: JsonSchema): ReturnSchema {
  const models: Record<string, ModelSpec> = {};
  for (const [name, def] of Object.entries(schema.$defs ?? {})) models[name] = modelOf(name, def);
  return { root: modelOf(schema.title ?? "IndividualReturn", schema), models };
}

/** Field names at the top of the schema, in order (so a section plan can be checked against the model). */
export function rootFieldNames(schema: ReturnSchema): string[] {
  return schema.root.fields.map((f) => f.name);
}

const DECIMAL = /^-?\d+(\.\d+)?$/;

/**
 * What a typed amount becomes on the wire: a plain decimal string ("1,234.50" -> "1234.50"), or null for nothing
 * typed. Anything else is kept as typed and reported invalid, never coerced.
 */
export function normaliseMoney(raw: string): { value: string | null; valid: boolean } {
  const text = raw.replace(/[$,\s]/g, "");
  if (text === "") return { value: null, valid: true };
  const parens = /^\((.*)\)$/.exec(text);
  const signed = parens?.[1] !== undefined ? `-${parens[1]}` : text;
  return { value: signed, valid: DECIMAL.test(signed) };
}

/** A stored value as the input shows it: decimal strings as they are, numbers as text, nothing for null. */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
