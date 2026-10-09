import { isApiError, type ClientDetail, type ReturnListItem } from "@agentledger/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState, type SyntheticEvent } from "react";

import { api } from "../../api";
import { useMe } from "../../auth/AuthProvider";
import { can } from "../../auth/can";
import { formatDateTime, formatMoney } from "../../lib/format";
import { queries, queryKeys } from "../../queries";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { StatusChip } from "../../ui/Chip";
import { DataTable } from "../../ui/DataTable";
import { FormError, InputField, SelectField } from "../../ui/Field";
import { Empty, QueryBoundary } from "../../ui/states";
import { useToast } from "../../ui/Toast";
import { useClientDetail } from "../clientDetail";
import styles from "../screens.module.css";
import { describeStatus } from "./returnState";

const FILING_STATUSES: { value: string; label: string }[] = [
  { value: "single", label: "Single" },
  { value: "mfj", label: "Married filing jointly" },
  { value: "mfs", label: "Married filing separately" },
  { value: "hoh", label: "Head of household" },
  { value: "qss", label: "Qualifying surviving spouse" },
];

export function ReturnsScreen() {
  const { query } = useClientDetail();
  return <QueryBoundary query={query}>{(detail) => <Returns detail={detail} />}</QueryBoundary>;
}

/** The refund or the amount owed, whichever the summary carries; "—" until the return is computed. */
export function outcomeOf(summary: ReturnListItem["summary"]): string {
  if (summary.refund && summary.refund !== "0") return `refund ${formatMoney(summary.refund)}`;
  if (summary.amount_owed && summary.amount_owed !== "0") return `owed ${formatMoney(summary.amount_owed)}`;
  if (summary.total_tax !== undefined) return `tax ${formatMoney(summary.total_tax)}`;
  return "—";
}

function Returns({ detail }: { detail: ClientDetail }) {
  const me = useMe();
  const { year } = useClientDetail();
  const clientId = detail.client.id;
  const list = useQuery(queries.returns(clientId));
  const create = can(me, "returns.create", { clientId });

  const columns = useMemo<ColumnDef<ReturnListItem>[]>(
    () => [
      {
        id: "form",
        header: "Return",
        accessorFn: (r) => `${r.tax_year} ${r.form}`,
        cell: ({ row }) => (
          <Link to="/returns/$rid" params={{ rid: row.original.id }}>
            <strong>
              Form {row.original.form} · {row.original.tax_year}
            </strong>
          </Link>
        ),
      },
      { id: "year", header: "Tax year", accessorFn: (r) => r.tax_year, meta: { align: "right" } },
      {
        id: "status",
        header: "Status",
        accessorFn: (r) => r.status,
        cell: ({ row }) => (
          <>
            <StatusChip status={row.original.status} />{" "}
            <span className={styles.meta}>{describeStatus(row.original.status)}</span>
          </>
        ),
      },
      { id: "version", header: "Version", accessorFn: (r) => r.version, meta: { align: "right" } },
      { id: "outcome", header: "Outcome", accessorFn: (r) => outcomeOf(r.summary), meta: { align: "right" } },
      {
        id: "created",
        header: "Created",
        accessorFn: (r) => r.created_at,
        cell: ({ row }) => `${formatDateTime(row.original.created_at)} · ${row.original.created_by}`,
      },
    ],
    [],
  );

  return (
    <>
      <Card>
        <QueryBoundary
          query={list}
          loadingLabel="Loading returns"
          isEmpty={(rows) => rows.length === 0}
          empty={
            <Empty title="No returns yet">
              <p>Create the Form 1040 for a tax year, then populate it from the documents on file.</p>
            </Empty>
          }
        >
          {(rows) => (
            <DataTable
              data={rows}
              columns={columns}
              caption={`Returns of ${detail.client.name}`}
              getRowId={(r) => r.id}
              initialSorting={[{ id: "year", desc: true }]}
            />
          )}
        </QueryBoundary>
      </Card>
      <CreateReturn
        clientId={clientId}
        defaultYear={year}
        disabledReason={create.allowed ? undefined : create.reason}
      />
    </>
  );
}

function CreateReturn({
  clientId,
  defaultYear,
  disabledReason,
}: {
  clientId: string;
  defaultYear: number;
  disabledReason: string | undefined;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [taxYear, setTaxYear] = useState(String(defaultYear));
  const [filingStatus, setFilingStatus] = useState("single");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [ssn, setSsn] = useState("");
  const [dob, setDob] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => {
      const taxpayer: Record<string, string> = {};
      if (firstName.trim()) taxpayer.first_name = firstName.trim();
      if (lastName.trim()) taxpayer.last_name = lastName.trim();
      if (ssn.trim()) taxpayer.ssn = ssn.trim();
      if (dob) taxpayer.dob = dob;
      return api.returns.create(clientId, {
        tax_year: Number(taxYear),
        inputs: { filing_status: filingStatus, taxpayer },
      });
    },
    onSuccess: async (created) => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: queryKeys.returns(clientId) }),
        qc.invalidateQueries({ queryKey: queryKeys.client(clientId) }),
      ]);
      toast(`Form 1040 for ${taxYear} created.`, "success");
      await navigate({ to: "/returns/$rid", params: { rid: created.id } });
    },
    onError: (err) => setError(isApiError(err) ? err.message : "The return could not be created."),
  });

  function submit(e: SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    create.mutate();
  }

  return (
    <Card title="Create a Form 1040" className={styles.form}>
      <p className={styles.hint}>
        Only what the documents cannot say is entered here; everything else is populated from the documents on file,
        with its provenance. A missing amount is never taken as zero.
      </p>
      <FormError message={error} />
      <form onSubmit={submit} aria-label="Create a Form 1040">
        <div className={styles.formRow}>
          <InputField
            label="Tax year"
            type="number"
            inputMode="numeric"
            min={2000}
            max={2100}
            required
            value={taxYear}
            onChange={(e) => setTaxYear(e.target.value)}
          />
          <SelectField label="Filing status" value={filingStatus} onChange={(e) => setFilingStatus(e.target.value)}>
            {FILING_STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </SelectField>
        </div>
        <div className={styles.formRow}>
          <InputField
            label="Taxpayer first name"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            autoComplete="off"
          />
          <InputField
            label="Taxpayer last name"
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
            autoComplete="off"
          />
        </div>
        <div className={styles.formRow}>
          <InputField
            label="Taxpayer SSN"
            hint="Without it the return computes with a blocking diagnostic (taxpayer_ssn_missing)."
            inputMode="numeric"
            pattern="\d{3}-?\d{2}-?\d{4}"
            value={ssn}
            onChange={(e) => setSsn(e.target.value)}
            autoComplete="off"
          />
          <InputField label="Taxpayer date of birth" type="date" value={dob} onChange={(e) => setDob(e.target.value)} />
        </div>
        <div className={styles.actions}>
          <Button type="submit" tone="primary" busy={create.isPending} disabledReason={disabledReason}>
            Create return
          </Button>
        </div>
      </form>
    </Card>
  );
}
