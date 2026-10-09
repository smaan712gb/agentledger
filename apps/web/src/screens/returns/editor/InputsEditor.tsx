/**
 * The inputs editor, generated from the IndividualReturn JSON schema: sections of fields, nested groups, lists of
 * items (each from a document or entered by hand) and keyed maps (box 12 codes). Every field shows where its value
 * came from (the provenance chip opens the document beside it), whether an open fact conflict names it, and what a
 * blank means here: "not stated" for an Optional field, the model's default otherwise, and for a document item's
 * required amount the engine's rule, never zero.
 */

import type { FactConflict, Provenance } from "@agentledger/contracts";
import returnSchema from "@agentledger/contracts/return-schema.json";
import { useId, useState, type ReactNode } from "react";

import { Button } from "../../../ui/Button";
import { Card } from "../../../ui/Card";
import { Chip } from "../../../ui/Chip";
import { fieldLabel, humanise, itemTitle, modelTitle, planSections } from "./labels";
import { getPath, isRecord, itemIdentity, parsePath } from "./paths";
import { ProvenanceChip } from "./ProvenanceChip";
import { isEmptyValue, neverZero } from "./rules";
import {
  compileSchema,
  displayValue,
  normaliseMoney,
  rootFieldNames,
  type FieldKind,
  type FieldSpec,
  type JsonSchema,
  type ReturnSchema,
} from "./schema";
import styles from "../returns.module.css";

const schemaDocument: JsonSchema = returnSchema;
export const RETURN_SCHEMA: ReturnSchema = compileSchema(schemaDocument);
export const ROOT_MODEL = "IndividualReturn";

export interface EditorProps {
  inputs: Record<string, unknown>;
  provenance: Record<string, Provenance>;
  /** Open conflicts by the positional path they name today. */
  conflicts: Record<string, FactConflict>;
  /** Field errors by path: the API's 422 messages and the editor's own (an amount that is not a number). */
  errors: Record<string, string>;
  disabled: boolean;
  selected: string | null;
  onSelect: (path: string) => void;
  /** `undefined` removes the key (the model's default applies); `problem` marks the field invalid until fixed. */
  onChange: (path: string, value: unknown, problem?: string) => void;
}

interface NodeProps {
  model: string;
  spec: FieldSpec;
  path: string;
  ctx: EditorProps;
  /** The top-level list the field belongs to (the never-zero rule is per list). */
  listKey: string | null;
  label?: string;
}

export function fieldId(path: string): string {
  return `f-${path.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

/** The model and field spec a positional path points at, or null when the schema has no such field. */
export function fieldAt(schema: ReturnSchema, path: string): { model: string; spec: FieldSpec } | null {
  let modelName = ROOT_MODEL;
  let fields = schema.root.fields;
  let spec: FieldSpec | null = null;
  for (const seg of parsePath(path)) {
    if (typeof seg === "number") {
      if (spec?.kind.kind !== "list") return null;
      const item = spec.kind.item;
      if (item.kind !== "object") return { model: modelName, spec: { ...spec, kind: item } };
      modelName = item.model;
      fields = schema.models[item.model]?.fields ?? [];
      spec = null;
      continue;
    }
    if (spec?.kind.kind === "map") {
      return {
        model: modelName,
        spec: { name: seg, kind: spec.kind.value, nullable: false, required: false, default: undefined },
      };
    }
    if (spec?.kind.kind === "object") {
      modelName = spec.kind.model;
      fields = schema.models[spec.kind.model]?.fields ?? [];
    }
    const next = fields.find((f) => f.name === seg);
    if (!next) return null;
    spec = next;
  }
  return spec ? { model: modelName, spec } : null;
}

export function InputsEditor(props: EditorProps) {
  const sections = planSections(rootFieldNames(RETURN_SCHEMA));
  return (
    <div data-testid="inputs-editor">
      <nav className={styles.sectionNav} aria-label="Sections">
        <ul>
          {sections.map((s) => (
            <li key={s.id}>
              <a href={`#section-${s.id}`}>{s.title}</a>
            </li>
          ))}
        </ul>
      </nav>
      {sections.map((s) => (
        <Card key={s.id} id={`section-${s.id}`} title={s.title}>
          <div className={styles.fieldGrid}>
            {s.fields.map((name) => {
              const spec = RETURN_SCHEMA.root.fields.find((f) => f.name === name);
              return spec ? (
                <FieldNode key={name} model={ROOT_MODEL} spec={spec} path={name} ctx={props} listKey={null} />
              ) : null;
            })}
          </div>
        </Card>
      ))}
    </div>
  );
}

function FieldNode(props: NodeProps) {
  switch (props.spec.kind.kind) {
    case "object":
      return <GroupField {...props} model={props.spec.kind.model} />;
    case "list":
      return <ListField {...props} />;
    case "map":
      return <MapField {...props} />;
    default:
      return <ScalarField {...props} />;
  }
}

const FULL_ROW = { gridColumn: "1 / -1" } as const;

function GroupField({ model, spec, path, ctx, listKey, label }: NodeProps) {
  const title = label ?? fieldLabel(ROOT_MODEL === model ? ROOT_MODEL : parentModel(path), spec.name);
  const value = getPath(ctx.inputs, path);
  const present = isRecord(value);
  const fields = RETURN_SCHEMA.models[model]?.fields ?? [];
  if (spec.nullable && !present) {
    return (
      <div className={styles.field} style={FULL_ROW}>
        <span className={styles.label}>{title}</span>
        <div className={styles.meta}>
          <span className={[styles.cue, styles.cueNotStated].join(" ")}>not stated</span>
          <Button
            size="sm"
            onClick={() => ctx.onChange(path, {})}
            disabledReason={ctx.disabled ? "the inputs are locked" : undefined}
          >
            Add {title.toLowerCase()}
          </Button>
        </div>
      </div>
    );
  }
  return (
    <fieldset className={styles.fieldset} style={FULL_ROW}>
      <legend className={styles.legend}>
        {title}
        {spec.nullable ? (
          <Button
            size="sm"
            tone="ghost"
            onClick={() => ctx.onChange(path, undefined)}
            disabledReason={ctx.disabled ? "the inputs are locked" : undefined}
            aria-label={`Remove ${title}`}
          >
            Remove
          </Button>
        ) : null}
      </legend>
      <div className={styles.fieldGrid}>
        {fields.map((f) => (
          <FieldNode key={f.name} model={model} spec={f} path={`${path}.${f.name}`} ctx={ctx} listKey={listKey} />
        ))}
      </div>
    </fieldset>
  );
}

/** The model that owns the last field of a path (for labels inside groups and items). */
function parentModel(path: string): string {
  const segments = parsePath(path);
  const parent = segments.slice(0, -1);
  if (!parent.length) return ROOT_MODEL;
  const found = fieldAt(
    RETURN_SCHEMA,
    parent
      .map((s) => (typeof s === "number" ? `[${s}]` : s))
      .join(".")
      .replace(/\.\[/g, "["),
  );
  if (!found) return ROOT_MODEL;
  if (found.spec.kind.kind === "object") return found.spec.kind.model;
  if (found.spec.kind.kind === "list" && found.spec.kind.item.kind === "object") return found.spec.kind.item.model;
  return found.model;
}

function ListField({ model, spec, path, ctx }: NodeProps) {
  const title = fieldLabel(model, spec.name);
  const value = getPath(ctx.inputs, path);
  const items: unknown[] = Array.isArray(value) ? value : [];
  const itemKind: FieldKind = spec.kind.kind === "list" ? spec.kind.item : { kind: "text" };
  const topLevel = !path.includes(".") && !path.includes("[") ? path : null;
  const lock = ctx.disabled ? "the inputs are locked" : undefined;
  return (
    <fieldset className={styles.fieldset} style={FULL_ROW} data-testid={`list-${path}`}>
      <legend className={styles.legend}>
        {title} ({items.length})
      </legend>
      {items.map((item, i) => {
        const itemPath = `${path}[${i}]`;
        if (itemKind.kind !== "object") {
          return (
            <div key={itemPath} className={styles.rowActions}>
              <ScalarField
                model={model}
                spec={{ name: spec.name, kind: itemKind, nullable: false, required: true, default: undefined }}
                path={itemPath}
                ctx={ctx}
                listKey={topLevel}
                label={`${title} ${i + 1}`}
              />
              <Button
                size="sm"
                tone="ghost"
                onClick={() => ctx.onChange(itemPath, undefined)}
                disabledReason={lock}
                aria-label={`Remove ${title} ${i + 1}`}
              >
                Remove
              </Button>
            </div>
          );
        }
        const record = isRecord(item) ? item : {};
        const identity = itemIdentity(item);
        const heading = itemTitle(itemKind.model, i, record);
        return (
          <fieldset key={itemPath} className={styles.fieldset} data-testid={`item-${itemPath}`}>
            <legend className={styles.legend}>
              {heading}
              {identity ? (
                <Chip tone="info" title="This item was read from a document; its identity leaves with it.">
                  from document {identity}
                </Chip>
              ) : (
                <Chip>entered by hand</Chip>
              )}
              <Button
                size="sm"
                tone="ghost"
                onClick={() => ctx.onChange(itemPath, undefined)}
                disabledReason={lock}
                aria-label={`Remove ${heading}`}
              >
                Remove
              </Button>
            </legend>
            <div className={styles.fieldGrid}>
              {(RETURN_SCHEMA.models[itemKind.model]?.fields ?? []).map((f) => (
                <FieldNode
                  key={f.name}
                  model={itemKind.model}
                  spec={f}
                  path={`${itemPath}.${f.name}`}
                  ctx={ctx}
                  listKey={topLevel}
                />
              ))}
            </div>
          </fieldset>
        );
      })}
      <div className={styles.rowActions}>
        <Button
          size="sm"
          onClick={() => ctx.onChange(`${path}[${items.length}]`, itemKind.kind === "object" ? {} : "")}
          disabledReason={lock}
        >
          Add {itemKind.kind === "object" ? modelTitle(itemKind.model).toLowerCase() : title.toLowerCase()}
        </Button>
        {itemKind.kind === "object" ? (
          <span className={styles.meta}>
            Items from documents are added by populating; an item added here is entered by hand.
          </span>
        ) : null}
      </div>
    </fieldset>
  );
}

function MapField({ model, spec, path, ctx, listKey }: NodeProps) {
  const title = fieldLabel(model, spec.name);
  const value = getPath(ctx.inputs, path);
  const record = isRecord(value) ? value : {};
  const valueKind: FieldKind = spec.kind.kind === "map" ? spec.kind.value : { kind: "text" };
  const keys = spec.kind.kind === "map" ? spec.kind.keys : null;
  const lock = ctx.disabled ? "the inputs are locked" : undefined;
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const ids = { key: useId(), value: useId() };
  const free = keys ? keys.filter((k) => !(k in record)) : null;

  function add() {
    const key = newKey.trim();
    if (!key) {
      setProblem("Enter the code or key.");
      return;
    }
    if (key in record) {
      setProblem(`${key} is already there.`);
      return;
    }
    let stored: unknown = newValue;
    if (valueKind.kind === "money") {
      const { value: amount, valid } = normaliseMoney(newValue);
      if (!valid || amount === null) {
        setProblem("Enter the amount as a plain number, like 1234.50.");
        return;
      }
      stored = amount;
    }
    ctx.onChange(`${path}.${key}`, stored);
    setNewKey("");
    setNewValue("");
    setProblem(null);
  }

  return (
    <fieldset className={styles.fieldset} style={FULL_ROW}>
      <legend className={styles.legend}>{title}</legend>
      {Object.entries(record).map(([k]) => (
        <div key={k} className={styles.mapRow}>
          <span className={styles.label}>{humanise(k)}</span>
          <ScalarField
            model={model}
            spec={{ name: k, kind: valueKind, nullable: false, required: true, default: undefined }}
            path={`${path}.${k}`}
            ctx={ctx}
            listKey={listKey}
            label={`${title}: ${k}`}
          />
          <Button
            size="sm"
            tone="ghost"
            onClick={() => ctx.onChange(`${path}.${k}`, undefined)}
            disabledReason={lock}
            aria-label={`Remove ${k}`}
          >
            Remove
          </Button>
        </div>
      ))}
      <div className={styles.mapRow}>
        <div className={styles.field}>
          <label htmlFor={ids.key} className={styles.label}>
            {keys ? "Key" : "Code or key"}
          </label>
          {free ? (
            <select
              id={ids.key}
              className={styles.control}
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              disabled={Boolean(lock)}
            >
              <option value="">choose…</option>
              {free.map((k) => (
                <option key={k} value={k}>
                  {humanise(k)}
                </option>
              ))}
            </select>
          ) : (
            <input
              id={ids.key}
              className={styles.control}
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              disabled={Boolean(lock)}
            />
          )}
        </div>
        <div className={styles.field}>
          <label htmlFor={ids.value} className={styles.label}>
            Amount
          </label>
          <input
            id={ids.value}
            className={[styles.control, valueKind.kind === "money" ? styles.money : ""].join(" ")}
            inputMode={valueKind.kind === "money" ? "decimal" : undefined}
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            disabled={Boolean(lock)}
            aria-describedby={problem ? `${ids.value}-problem` : undefined}
            aria-invalid={problem ? true : undefined}
          />
        </div>
        <Button size="sm" onClick={add} disabledReason={lock}>
          Add
        </Button>
      </div>
      {problem ? (
        <div id={`${ids.value}-problem`} className={styles.error} role="alert">
          {problem}
        </div>
      ) : null}
    </fieldset>
  );
}

interface Cue {
  className: string;
  text: string;
}

/** What a blank means here: the engine's never-zero rule, "not stated", or the model's default. */
export function blankCue(spec: FieldSpec, listKey: string | null): Cue | null {
  const rule = neverZero(listKey, spec.name);
  if (rule) {
    const text =
      rule.kind === "required"
        ? "missing — never taken as 0"
        : rule.kind === "implied"
          ? `missing — implied by ${humanise(rule.by).toLowerCase()}, never taken as 0`
          : "missing — a valid code is required, never defaulted";
    return { className: styles.cueMissing ?? "", text };
  }
  if (spec.nullable) return { className: styles.cueNotStated ?? "", text: "not stated (not 0)" };
  if (spec.required) return { className: styles.cueMissing ?? "", text: "required" };
  if (spec.default !== undefined && spec.default !== null) {
    const shown = displayValue(spec.default);
    return { className: styles.cueNotStated ?? "", text: shown === "" ? "blank" : `blank = default ${shown}` };
  }
  return null;
}

function ScalarField({ model, spec, path, ctx, listKey, label }: NodeProps) {
  const id = fieldId(path);
  const value = getPath(ctx.inputs, path);
  const text = label ?? fieldLabel(model, spec.name);
  const p = ctx.provenance[path];
  const conflict = ctx.conflicts[path];
  const error = ctx.errors[path];
  const cue = isEmptyValue(value) ? blankCue(spec, listKey) : null;
  const describedBy =
    [cue ? `${id}-cue` : null, error ? `${id}-error` : null, p ? `${id}-prov` : null].filter(Boolean).join(" ") ||
    undefined;
  const common = {
    id,
    disabled: ctx.disabled,
    "aria-describedby": describedBy,
    "aria-invalid": error ? (true as const) : undefined,
    onFocus: () => ctx.onSelect(path),
  };
  return (
    <div className={[styles.field, ctx.selected === path ? styles.selected : ""].join(" ")} data-path={path}>
      <label htmlFor={id} className={styles.label}>
        {text}
        {spec.required ? " *" : ""}
      </label>
      <Control spec={spec} value={value} common={common} onChange={(v, problem) => ctx.onChange(path, v, problem)} />
      <div className={styles.meta}>
        {cue ? (
          <span id={`${id}-cue`} className={[styles.cue, cue.className].join(" ")}>
            {cue.text}
          </span>
        ) : null}
        {p ? (
          <ProvenanceChip
            id={`${id}-prov`}
            provenance={p}
            list={listKey}
            selected={ctx.selected === path}
            onSelect={() => ctx.onSelect(path)}
          />
        ) : null}
        {conflict ? (
          <a href="#conflicts" className={styles.cueMissing}>
            open conflict: the document says {displayValue(conflict.proposed_value) || "nothing"}
          </a>
        ) : null}
      </div>
      {error ? (
        <div id={`${id}-error`} className={styles.error} role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}

interface CommonAttrs {
  id: string;
  disabled: boolean;
  "aria-describedby": string | undefined;
  "aria-invalid": true | undefined;
  onFocus: () => void;
}

function Control({
  spec,
  value,
  common,
  onChange,
}: {
  spec: FieldSpec;
  value: unknown;
  common: CommonAttrs;
  onChange: (value: unknown, problem?: string) => void;
}): ReactNode {
  const kind = spec.kind;
  switch (kind.kind) {
    case "money":
      return <MoneyInput value={value} common={common} onChange={onChange} />;
    case "integer":
      return (
        <input
          {...common}
          type="number"
          step={1}
          min={kind.min}
          max={kind.max}
          className={[styles.control, styles.money].join(" ")}
          value={displayValue(value)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              onChange(undefined);
              return;
            }
            const n = Number(raw);
            onChange(Number.isInteger(n) ? n : raw, Number.isInteger(n) ? undefined : "Enter a whole number.");
          }}
        />
      );
    case "date":
      return (
        <input
          {...common}
          type="date"
          className={styles.control}
          value={displayValue(value)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        />
      );
    case "boolean":
      if (spec.nullable) {
        return (
          <select
            {...common}
            className={styles.control}
            value={value === true ? "true" : value === false ? "false" : ""}
            onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "true")}
          >
            <option value="">not stated</option>
            <option value="true">yes</option>
            <option value="false">no</option>
          </select>
        );
      }
      return (
        <input
          {...common}
          type="checkbox"
          checked={value === true || (value === undefined && spec.default === true)}
          onChange={(e) => onChange(e.target.checked)}
        />
      );
    case "enum":
      return (
        <select
          {...common}
          className={styles.control}
          value={displayValue(value) || (spec.nullable ? "" : displayValue(spec.default))}
          onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        >
          {spec.nullable ? <option value="">not stated</option> : null}
          {kind.options.map((o) => (
            <option key={o} value={o}>
              {humanise(o)}
            </option>
          ))}
        </select>
      );
    case "text-or-enum":
      return (
        <>
          <input
            {...common}
            type="text"
            className={styles.control}
            list={`${common.id}-options`}
            placeholder={`YYYY-MM-DD or ${kind.options.join(" / ")}`}
            value={displayValue(value)}
            onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
          />
          <datalist id={`${common.id}-options`}>
            {kind.options.map((o) => (
              <option key={o} value={o} />
            ))}
          </datalist>
        </>
      );
    default:
      return (
        <input
          {...common}
          type="text"
          className={styles.control}
          value={displayValue(value)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        />
      );
  }
}

/** Shows the stored decimal string; while focused, whatever is typed; on blur, the normalised amount is stored. */
function MoneyInput({
  value,
  common,
  onChange,
}: {
  value: unknown;
  common: CommonAttrs;
  onChange: (value: unknown, problem?: string) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const shown = text ?? displayValue(value);
  return (
    <input
      {...common}
      type="text"
      inputMode="decimal"
      className={[styles.control, styles.money].join(" ")}
      value={shown}
      placeholder="not stated"
      onFocus={() => {
        setText(displayValue(value));
        common.onFocus();
      }}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        if (text === null) return;
        const { value: amount, valid } = normaliseMoney(text);
        setText(null);
        if (!valid) onChange(text, "Enter the amount as a plain number, like 1234.50.");
        else if ((amount ?? undefined) !== (isEmptyValue(value) ? undefined : displayValue(value)))
          onChange(amount ?? undefined);
      }}
    />
  );
}
