/** Typed fixtures: every object is one of the contract's types, so a type change breaks the tests first. */

import type {
  AuthConfig,
  Client,
  ClientDetail,
  ClientDocument,
  DocumentVersion,
  Engagement,
  Firm,
  FirmUser,
  Health,
  Me,
  Pipeline,
  ReviewDocument,
} from "@agentledger/contracts";

export const firmAdmin: Me = {
  id: "u_admin",
  firm_id: "rivera-cpa",
  email: "maya@rivera.example",
  name: "Maya Rivera",
  role: "cpa",
  base_role: "firm_admin",
  client_id: null,
  reviewer: false,
  auth_method: "password+totp",
  fresh_at: "2026-10-09 10:00:00",
  session_id: "s1",
  mfa_enrolled_at: "2026-10-01 09:00:00",
  disabled: false,
  last_login_at: "2026-10-09 10:00:00",
  firm: { id: "rivera-cpa", name: "Rivera CPA", status: "active" },
};

export const cpa: Me = {
  ...firmAdmin,
  id: "u_cpa",
  email: "lee@rivera.example",
  name: "Lee Park",
  base_role: "cpa",
  reviewer: true,
};

export const staff: Me = {
  ...firmAdmin,
  id: "u_staff",
  email: "sam@rivera.example",
  name: "Sam Staff",
  base_role: "staff",
  reviewer: false,
  engaged: ["ortiz-auto"],
};

export const clientUser: Me = {
  ...firmAdmin,
  id: "u_client",
  email: "sam@ortiz.example",
  name: "Sam Ortiz",
  role: "client",
  base_role: "client",
  client_id: "ortiz-auto",
  reviewer: false,
};

export const platformAdmin: Me = {
  ...firmAdmin,
  id: "u_ops",
  firm_id: "_platform",
  email: "ops@agentledger.example",
  name: "Ops",
  role: "platform_admin",
  base_role: "platform_admin",
  firm: null,
};

export const localConfig: AuthConfig = { identity: "local", password_sign_in: true };
export const workosConfig: AuthConfig = { identity: "workos", password_sign_in: "platform administrators only" };

export const health: Health = { ok: true, build: "dev", backend: "sqlite" };

export const ortiz: Client = {
  id: "ortiz-auto",
  name: "Ortiz Auto",
  kind: "business",
  entity_type: "s_corp",
  formed_under: "domestic",
  tax_id_last4: "1234",
  emails: ["owner@ortiz.example"],
  aliases: [],
  consent_7216_at: null,
  closed_through: "2026-03-31",
  domain: "auto_repair",
  facts: { accounting_basis: "cash", employees: 5 },
  created_at: "2026-01-02 10:00:00",
};

export const lakeside: Client = {
  ...ortiz,
  id: "lakeside-fuel",
  name: "Lakeside Fuel",
  domain: "gas_station",
  entity_type: "llc",
  closed_through: null,
  facts: {},
  emails: [],
};

export const clients: Client[] = [ortiz, lakeside];

export const doc1: ClientDocument = {
  id: "doc_1",
  original_name: "1099int.txt",
  doc_type: "1099-INT",
  tax_year: 2025,
  status: "filed",
  confidence: 0.8,
  vault_path: "blob:ab/cd",
  summary: "Interest income statement",
  classified_by: "deterministic",
  received_at: "2026-02-01 12:00:00",
  channel: "upload",
};

export function detailOf(client: Client, year = 2026): ClientDetail {
  return {
    client,
    year,
    pack: {
      id: client.domain,
      title: client.domain.replaceAll("_", " "),
      description: "",
      facts: ["state", "employees"],
    },
    balances: [],
    kpis: [
      { id: "revenue", title: "Revenue", value: "125000.50", unit: "USD" },
      { id: "receipts_missing", title: "Receipts missing", value: "3", unit: "documents", missing: null },
    ],
    findings: [
      {
        id: "f1",
        client_id: client.id,
        first_seen: "2026-03-01 09:00:00",
        check_id: "unsupported_expense",
        severity: "medium",
        title: "Expense without a receipt",
        detail: "A $412.00 expense has no document.",
        citation: "IRC §6001",
        owner: "client",
        status: "open",
        resolutions: [],
      },
    ],
    documents: client.id === ortiz.id ? [doc1] : [],
    tasks: [
      {
        id: 1,
        client_id: client.id,
        client_name: client.name,
        title: "Send the March bank statement",
        detail: "",
        assignee: "client",
        status: "open",
        source: "automation",
        due: "2026-04-15",
      },
    ],
    deadlines: [{ title: "Form 1120-S", due: "2026-03-16", days: 30 }],
    opportunities: [],
    chain: { ok: true, checked: 12 },
    integrity: 84,
  };
}

export const review: ReviewDocument[] = [
  {
    id: "doc_r1",
    original_name: "scan.bin",
    doc_type: "Other",
    tax_year: null,
    confidence: 0,
    summary: null,
    sender: null,
    received_at: "2026-10-01 08:00:00",
    channel: "upload",
    classified_by: "deterministic",
  },
];

export const versions: DocumentVersion[] = [
  {
    document_id: "doc_1",
    version: 1,
    sha256: "ab".repeat(32),
    size: 1024,
    created_at: "2026-02-01 12:00:00",
    created_by: "u_admin",
    reason: "received",
  },
];

export const users: FirmUser[] = [
  {
    id: "u_admin",
    firm_id: "rivera-cpa",
    email: "maya@rivera.example",
    name: "Maya Rivera",
    role: "firm_admin",
    client_id: null,
    mfa_enrolled_at: "2026-10-01",
    disabled: false,
    last_login_at: "2026-10-09 10:00:00",
    reviewer: false,
  },
  {
    id: "u_cpa",
    firm_id: "rivera-cpa",
    email: "lee@rivera.example",
    name: "Lee Park",
    role: "cpa",
    client_id: null,
    mfa_enrolled_at: null,
    disabled: false,
    last_login_at: null,
    reviewer: true,
  },
];

export const firms: Firm[] = [
  { id: "rivera-cpa", name: "Rivera CPA", status: "active", created_at: "2026-01-01 00:00:00", deleted_at: null },
  { id: "lake-tax", name: "Lake Tax", status: "provisioning", created_at: "2026-02-01 00:00:00", deleted_at: null },
];

const engagement: Engagement = {
  id: 7,
  client_id: "ortiz-auto",
  client_name: "Ortiz Auto",
  type: "1120-S",
  tax_year: 2025,
  owner: "u_cpa",
  due_date: "2026-03-16",
  fee: "2500.00",
  stage: "in_progress",
  open_tasks: 1,
};

export const pipeline: Pipeline = {
  lead: [],
  proposal: [],
  engaged: [],
  in_progress: [engagement],
  client_review: [],
  filed: [],
  closed: [],
};
