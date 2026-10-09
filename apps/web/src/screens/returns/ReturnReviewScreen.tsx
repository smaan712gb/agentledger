import { isApiError, type FactConflict } from "@agentledger/contracts";
import { useMutation } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useState } from "react";

import { api } from "../../api";
import { can } from "../../auth/can";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Dialog } from "../../ui/Dialog";
import { FormError } from "../../ui/Field";
import { Conflict, Frozen, ReadOnly, STATE_NAMES, StatePanel } from "../../ui/states";
import { useToast } from "../../ui/Toast";
import screens from "../screens.module.css";
import { ConflictsPanel } from "./editor/ConflictsPanel";
import { DispositionsPanel } from "./editor/DispositionsPanel";
import { DocumentViewer } from "./editor/DocumentViewer";
import { FactHistory } from "./editor/FactHistory";
import { InputsEditor, RETURN_SCHEMA, fieldAt } from "./editor/InputsEditor";
import { boxLabel, fieldLabel } from "./editor/labels";
import { anchorFor, listOf, locToPath, locateAnchor, setPath } from "./editor/paths";
import { displayValue } from "./editor/schema";
import { describeProvenance } from "./editor/ProvenanceChip";
import { PrepareActions } from "./prepare";
import { editability, splitReasons } from "./returnState";
import styles from "./returns.module.css";
import { useReturnWorkspace } from "./useReturnWorkspace";

interface Draft {
  version: number;
  inputs: Record<string, unknown>;
}

interface OpenDocument {
  documentId: string;
  box: string | null | undefined;
  list: string | null;
  value: string | undefined;
}

export function ReturnReviewScreen() {
  const { rid } = useParams({ from: "/_app/returns/$rid/review" });
  const ws = useReturnWorkspace(rid);
  const { toast } = useToast();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [clientErrors, setClientErrors] = useState<Record<string, string>>({});
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const [unlockedAt, setUnlockedAt] = useState<string | null>(null);
  const [askUnlock, setAskUnlock] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [opened, setOpened] = useState<OpenDocument | null>(null);

  const save = useMutation({
    mutationFn: (inputs: Record<string, unknown>) => api.returns.saveInputs(rid, inputs),
    onSuccess: async (result) => {
      setDraft(null);
      setServerErrors({});
      setFormError(null);
      const errors = result.diagnostics.filter((d) => d.severity === "error").length;
      toast(
        errors ? `Saved and recomputed with ${errors} blocking diagnostic(s).` : "Saved and recomputed.",
        "success",
      );
      await ws.invalidate();
    },
    onError: (err) => {
      if (!isApiError(err)) {
        setFormError("The inputs could not be saved.");
        return;
      }
      if (err.status === 422 && typeof err.detail !== "string") {
        const mapped: Record<string, string> = {};
        for (const e of err.detail) mapped[locToPath(e.loc)] = e.msg;
        setServerErrors(mapped);
        setFormError("The model refused these inputs; check the highlighted fields.");
        return;
      }
      if (err.status === 409) {
        setRefused(err.message);
        return;
      }
      setFormError(err.message);
    },
  });

  const detail = ws.detail.data;
  if (!detail) return null;
  if (detail.inputs === undefined) {
    return <ReadOnly reason="The working papers of a return are shown to firm staff only." compact={false} />;
  }
  const serverInputs = detail.inputs;
  const provenance = detail.provenance ?? {};
  const stale = draft !== null && draft.version !== detail.version;
  const inputs = draft && !stale ? draft.inputs : serverInputs;
  const dirty = draft !== null && !stale;
  const edit = editability(detail.status);
  const decision = can(ws.me, "returns.prepare", { clientId: detail.return.client_id });
  const unlocked = unlockedAt === detail.status;
  const lockedReason = !decision.allowed
    ? decision.reason
    : edit.mode === "frozen"
      ? edit.reason
      : edit.mode === "bound" && !unlocked
        ? "Unlock the inputs first: saving a change reopens the return."
        : undefined;
  const conflicts = ws.conflicts.data ?? [];
  const conflictsByPath: Record<string, FactConflict> = {};
  for (const c of conflicts) {
    const path = locateAnchor(c.anchor, inputs);
    if (path) conflictsByPath[path] = c;
  }
  const errors = { ...serverErrors, ...clientErrors };
  const invalid = Object.keys(clientErrors).length > 0;

  function change(path: string, value: unknown, problem?: string) {
    setDraft({ version: detail?.version ?? 0, inputs: setPath(inputs, path, value) });
    setClientErrors((prev) => {
      const next = Object.fromEntries(Object.entries(prev).filter(([k]) => k !== path));
      return problem ? { ...next, [path]: problem } : next;
    });
    setServerErrors((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => k !== path)));
  }

  function openDocument(documentId: string, box: string | null | undefined, list: string | null, value?: string) {
    setOpened({ documentId, box, list, value });
  }

  function select(path: string) {
    setSelected(path);
    const p = provenance[path];
    if (p?.document_id) openDocument(p.document_id, p.box, listOf(path), p.value);
  }

  const selectedField = selected ? fieldAt(RETURN_SCHEMA, selected) : null;
  const selectedProvenance = selected ? provenance[selected] : undefined;

  return (
    <>
      {edit.mode === "frozen" ? (
        <Frozen reason={`${edit.reason} ${edit.path}`} compact={false} />
      ) : edit.mode === "bound" ? (
        <StatePanel
          title={STATE_NAMES.frozen}
          tone="info"
          state="frozen"
          actions={
            unlocked ? (
              <span className={screens.meta}>Unlocked: saving will reopen the return.</span>
            ) : (
              <Button
                onClick={() => setAskUnlock(true)}
                disabledReason={decision.allowed ? undefined : decision.reason}
              >
                Unlock to edit
              </Button>
            )
          }
        >
          <p>
            {edit.reason} {edit.consequence}
          </p>
        </StatePanel>
      ) : null}
      {stale ? (
        <Conflict
          detail={`The return changed underneath you (version ${draft.version} became ${detail.version}): your unsaved edits are kept aside until you discard them and reload the inputs.`}
          onRefresh={() => {
            setDraft(null);
            setClientErrors({});
          }}
        />
      ) : null}
      {refused ? (
        <StatePanel
          title={STATE_NAMES.conflict}
          tone="warn"
          role="alert"
          state="conflict"
          actions={
            <Button
              onClick={() => {
                setRefused(null);
                void ws.invalidate();
              }}
            >
              Refresh
            </Button>
          }
        >
          <p>The workflow refused the change:</p>
          <ul className={styles.reasons}>
            {splitReasons(refused).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </StatePanel>
      ) : null}

      <div className={styles.workspace}>
        <div>
          <FormError message={formError} />
          <InputsEditor
            inputs={inputs}
            provenance={provenance}
            conflicts={conflictsByPath}
            errors={errors}
            disabled={lockedReason !== undefined}
            selected={selected}
            onSelect={select}
            onChange={change}
          />
          <div className={styles.saveBar} role="region" aria-label="Save changes">
            <span className={screens.meta} data-testid="dirty-state">
              {dirty ? "Unsaved changes." : "No unsaved changes."}
            </span>
            <Button
              tone="primary"
              busy={save.isPending}
              disabledReason={
                lockedReason ?? (!dirty ? "nothing changed" : invalid ? "fix the highlighted amounts first" : undefined)
              }
              onClick={() => save.mutate(inputs)}
            >
              Save and recompute
            </Button>
            <Button
              tone="ghost"
              disabledReason={dirty ? undefined : "nothing to discard"}
              onClick={() => {
                setDraft(null);
                setClientErrors({});
                setServerErrors({});
                setFormError(null);
              }}
            >
              Discard
            </Button>
            <span className={screens.meta}>
              A blank amount is "not stated", never 0; every save is a new version with its provenance on record.
            </span>
          </div>
        </div>

        <aside className={styles.side} aria-label="Preparation, provenance and documents">
          <Card title="Prepare">
            <PrepareActions ws={ws} detail={detail} onRefused={setRefused} />
          </Card>

          <Card title="Selected field" data-testid="selected-field">
            {selected && selectedField ? (
              <>
                <p>
                  <strong>{fieldLabel(selectedField.model, selectedField.spec.name)}</strong>{" "}
                  <code className={styles.code}>{selected}</code>
                </p>
                <p>
                  Value: {displayValue(selectedProvenance?.value ?? undefined) || "—"}
                  {selectedProvenance ? (
                    <>
                      {" · "}
                      {describeProvenance(selectedProvenance, listOf(selected)).detail}
                    </>
                  ) : (
                    " · no provenance recorded: entered by hand or not yet set"
                  )}
                </p>
                <FactHistory rid={rid} anchor={anchorFor(selected, inputs)} />
              </>
            ) : (
              <p className={screens.hint}>
                Focus a field, or press its provenance chip, to see where its value came from, every value it has had,
                and the document beside it.
              </p>
            )}
          </Card>

          {opened ? (
            <Card title="Document" data-testid="document-viewer">
              <DocumentViewer
                documentId={opened.documentId}
                name={ws.documents.find((d) => d.id === opened.documentId)?.original_name}
                box={boxLabel(opened.list, opened.box)}
                value={opened.value}
                onClose={() => setOpened(null)}
              />
            </Card>
          ) : null}

          <Card title={`Conflicts (${conflicts.length})`} id="conflicts">
            <ConflictsPanel
              rid={rid}
              conflicts={conflicts}
              inputs={inputs}
              disabled={lockedReason}
              onResolved={ws.invalidate}
              onOpenDocument={(c) =>
                openDocument(
                  c.document_id,
                  c.box,
                  listOf(locateAnchor(c.anchor, inputs) ?? ""),
                  displayValue(c.proposed_value),
                )
              }
              onLocate={(path) => {
                setSelected(path);
                document.getElementById(`f-${path.replace(/[^A-Za-z0-9_-]/g, "-")}`)?.focus();
              }}
            />
          </Card>

          <Card title="Documents of the year" id="documents">
            <DispositionsPanel
              ws={ws}
              detail={detail}
              disabled={lockedReason}
              onOpenDocument={(id) => openDocument(id, null, null)}
            />
          </Card>
        </aside>
      </div>

      <Dialog
        open={askUnlock}
        onOpenChange={(isOpen) => {
          if (!isOpen) setAskUnlock(false);
        }}
        title="Unlock the inputs?"
        description={edit.mode === "bound" ? edit.consequence : undefined}
      >
        <div className={screens.actions}>
          <Button onClick={() => setAskUnlock(false)}>Keep it locked</Button>
          <Button
            tone="danger"
            onClick={() => {
              setUnlockedAt(detail.status);
              setAskUnlock(false);
            }}
          >
            Unlock: I understand saving reopens the return
          </Button>
        </div>
      </Dialog>
    </>
  );
}
