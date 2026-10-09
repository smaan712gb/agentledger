/** Typed fixtures: every object is one of the contract's types, so a type change breaks the tests first. */

import type {
  AuthConfig,
  Client,
  ClientDetail,
  ClientDocument,
  Disposition,
  DocumentVersion,
  Engagement,
  FactAssertion,
  FactConflict,
  Firm,
  FirmUser,
  Health,
  Me,
  Pipeline,
  PopulateResult,
  Provenance,
  ReturnDetail,
  ReturnInputs,
  ReturnListItem,
  ReturnResult,
  ReturnRow,
  ReturnStatus,
  ReviewDocument,
  WorkflowEvent,
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

/** The two W-2s of the return fixture (one read in full, one whose wages intake could not read) and a 1099-NEC it does not use. */
export const docW2a: ClientDocument = {
  ...doc1,
  id: "doc_w2a",
  original_name: "w2-brightline-2026.pdf",
  doc_type: "W-2",
  tax_year: 2026,
  summary: "2026 Form W-2 from Brightline LLC",
  classified_by: "fixture:77022c94f0e4",
  received_at: "2026-02-03 12:00:00",
};
export const docW2b: ClientDocument = {
  ...docW2a,
  id: "doc_w2b",
  original_name: "w2-harbor-2026.pdf",
  summary: "2026 Form W-2 from Harbor Coffee Co.",
  received_at: "2026-02-04 12:00:00",
};
export const docNec: ClientDocument = {
  ...docW2a,
  id: "doc_nec",
  original_name: "1099nec-gig.pdf",
  doc_type: "1099-NEC",
  summary: "2026 Form 1099-NEC from Gig Platform",
  received_at: "2026-02-05 12:00:00",
};
export const ortizDocuments: ClientDocument[] = [docNec, docW2b, docW2a, doc1];

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

// ------------------------------------------------------------------------------------------------ returns

export const returnRow: ReturnRow = {
  id: "ret_1",
  client_id: "ortiz-auto",
  tax_year: 2026,
  form: "1040",
  created_at: "2026-10-01 09:00:00",
  created_by: "u_admin",
  amends: null,
};

/** As stored: W-2 1 with every box (its wages edited by the preparer over the document), W-2 2 without its wages. */
export const returnInputs: ReturnInputs = {
  tax_year: 2026,
  filing_status: "single",
  taxpayer: { first_name: "Jordan", last_name: "Lee", ssn: "400-00-0009", dob: "1990-01-01" },
  w2s: [
    {
      owner: "taxpayer",
      source_document: "doc_w2a",
      employer_name: "Brightline LLC",
      wages: "61000",
      federal_withholding: "6400.00",
      ss_wages: "61200.00",
      ss_tax: "3794.40",
      medicare_wages: "61200.00",
      medicare_tax: "887.40",
    },
    {
      owner: "taxpayer",
      source_document: "doc_w2b",
      employer_name: "Harbor Coffee Co.",
      federal_withholding: "600.00",
      ss_wages: "8400.00",
      ss_tax: "520.80",
      medicare_wages: "8400.00",
      medicare_tax: "121.80",
    },
  ],
};

const fromDoc = (document_id: string, box: string, value: string, confirmed = false): Provenance => ({
  document_id,
  box,
  value,
  confirmed,
});

export const returnProvenance: Record<string, Provenance> = {
  "w2s[0].wages": {
    source: "preparer",
    edited_by: "u_admin",
    previous_document: "doc_w2a",
    previous_value: "61200.00",
    previous_source: "document",
    confirmed: true,
  },
  "w2s[0].federal_withholding": fromDoc("doc_w2a", "box2", "6400.00"),
  "w2s[0].ss_wages": fromDoc("doc_w2a", "box3", "61200.00", true),
  "w2s[0].ss_tax": fromDoc("doc_w2a", "box4", "3794.40", true),
  "w2s[0].medicare_wages": fromDoc("doc_w2a", "box5", "61200.00", true),
  "w2s[0].medicare_tax": fromDoc("doc_w2a", "box6", "887.40", true),
  "w2s[1].federal_withholding": fromDoc("doc_w2b", "box2", "600.00"),
  "w2s[1].ss_wages": fromDoc("doc_w2b", "box3", "8400.00"),
  "w2s[1].ss_tax": fromDoc("doc_w2b", "box4", "520.80"),
  "w2s[1].medicare_wages": fromDoc("doc_w2b", "box5", "8400.00"),
  "w2s[1].medicare_tax": fromDoc("doc_w2b", "box6", "121.80"),
};

export const returnResult: ReturnResult = {
  tax_year: 2026,
  filing_status: "single",
  forms: { f1040: { "1a": "61000", "11a": "61000", "15": "45000", "24c": "5000", "33": "7000", "35a": "2000" } },
  summary: {
    agi: "61000",
    taxable_income: "45000",
    total_tax: "5000",
    payments: "7000",
    refund: "2000",
    amount_owed: "0",
  },
  diagnostics: [
    {
      severity: "warning",
      code: "w2_box1_differs_from_box3",
      message: "Box 1 and box 3 differ.",
      form: "f1040",
      line: "1a",
    },
  ],
  coverage: {
    forms: { f1040: "manual-assisted" },
    lowest: "manual-assisted",
    flags: [],
    below_preparation: [],
    filing_blockers: [{ form: "mef_1040", status: "unsupported", need: "filing-approved", limits: [] }],
  },
  pinned: { kb_version: "2026.3", engine: "1040-2026.3" },
};

const started: WorkflowEvent = {
  seq: 1,
  event: "started",
  actor: "u_admin",
  at: "2026-10-01T09:00:00+00:00",
  status: "preparing",
  note: "",
};

export const returnDetail: ReturnDetail = {
  return: returnRow,
  version: 3,
  status: "preparing",
  history: [started],
  summary: returnResult.summary,
  allowed: ["submit_for_review", "void"],
  waiting_on: null,
  crosscheck: null,
  filing: { status: "preparing", release: null, submissions: [], complete: false },
  inputs: returnInputs,
  provenance: returnProvenance,
  result: returnResult,
};

/** A return just created: nothing computed, nothing populated. */
export const freshReturn: ReturnDetail = {
  ...returnDetail,
  return: { ...returnRow, id: "ret_new" },
  version: 1,
  summary: {},
  inputs: { tax_year: 2026, filing_status: "single", taxpayer: {} },
  provenance: {},
  result: null,
};

const ALLOWED: Record<string, string[]> = {
  preparing: ["submit_for_review", "void"],
  in_review: ["approve", "reopen", "request_changes", "void"],
  approved: ["reopen", "request_signature", "void"],
  awaiting_signature: ["reopen", "signed", "void"],
  signed: ["approve_release", "mark_paper_filed", "outcome_unknown", "reopen", "transmit", "void"],
  release_approved: ["mark_paper_filed", "outcome_unknown", "reopen", "transmit", "void"],
  transmitted: ["ack_accepted", "ack_rejected"],
  accepted: [],
  rejected: ["correct", "void"],
  paper_filed: [],
  unknown: ["reconciled_not_submitted", "reconciled_submitted"],
  void: [],
};
const WAITING: Record<string, string> = {
  awaiting_signature: "taxpayer signature on Form 8879",
  transmitted: "IRS acknowledgement",
  unknown: "reconciliation with the transmitter",
  release_approved: "transmission by the workflow",
  in_review: "reviewer",
  approved: "signature request",
};

/** The fixture return in another status, with the history that gets it there (submitted by u_admin, approved by u_cpa). */
export function returnWith(status: ReturnStatus, submittedBy = "u_admin"): ReturnDetail {
  const steps: [ReturnStatus, string, string][] = [
    ["in_review", "submit_for_review", submittedBy],
    ["approved", "approve", "u_cpa"],
    ["awaiting_signature", "request_signature", "u_cpa"],
    ["signed", "signed", "taxpayer"],
    ["release_approved", "approve_release", "u_cpa"],
    ["transmitted", "transmit", "system"],
    ["accepted", "ack_accepted", "mef-poller"],
  ];
  const history: WorkflowEvent[] = [started];
  for (const [s, event, actor] of steps) {
    history.push({
      seq: history.length + 1,
      event,
      actor,
      at: `2026-10-0${history.length + 1}T09:00:00+00:00`,
      status: s,
      note: "",
    });
    if (s === status) break;
  }
  if (status === "unknown") {
    history.push({
      seq: history.length + 1,
      event: "outcome_unknown",
      actor: "system",
      at: "2026-10-09T09:00:00+00:00",
      status: "unknown",
      note: "timeout after sending",
    });
  }
  if (status === "void") {
    history.push({
      seq: history.length + 1,
      event: "void",
      actor: "u_cpa",
      at: "2026-10-09T09:00:00+00:00",
      status: "void",
      note: "filed with other software on 2027-04-10, transcript on file",
    });
  }
  return {
    ...returnDetail,
    status,
    history,
    allowed: ALLOWED[status] ?? [],
    waiting_on: WAITING[status] ?? null,
    filing: { ...returnDetail.filing, status },
  };
}

export const returnList: ReturnListItem[] = [
  { ...returnRow, status: "preparing", version: 3, summary: returnResult.summary },
];

export const conflicts: FactConflict[] = [
  {
    id: 1,
    return_id: "ret_1",
    path: "w2s[0].wages",
    anchor: "w2s[doc_w2a].wages",
    document_id: "doc_w2a",
    box: "box1",
    current_value: "61000",
    proposed_value: "61200.00",
    current_source: "preparer",
    raised_by: "u_admin",
    raised_at: "2026-10-02 10:00:00",
    resolution: null,
    resolved_by: null,
    resolved_at: null,
    note: null,
  },
  {
    id: 2,
    return_id: "ret_1",
    path: "w2s[1].wages",
    anchor: "missing:w2s[doc_w2b].wages",
    document_id: "doc_w2b",
    box: "required amount",
    current_value: null,
    proposed_value: null,
    current_source: "missing",
    raised_by: "u_admin",
    raised_at: "2026-10-02 10:00:00",
    resolution: null,
    resolved_by: null,
    resolved_at: null,
    note: null,
  },
];

export const factHistory: FactAssertion[] = [
  {
    id: 11,
    return_id: "ret_1",
    path: "w2s[doc_w2a].federal_withholding",
    value: "6400.00",
    source: "document",
    source_ref: "doc_w2a",
    asserted_by: "u_admin",
    asserted_at: "2026-10-02 10:00:00",
    supersedes: null,
  },
];

export const populateResult: PopulateResult = {
  documents: ["doc_w2a", "doc_w2b"],
  issues: [
    {
      document_id: "doc_nec",
      code: "nec_needs_business",
      message:
        "1099nec-gig.pdf: nonemployee compensation of 1200 needs a Schedule C business; attach it to a business on the return.",
    },
  ],
  fields: 11,
  conflicts: 2,
  superseded: 0,
};

export function dispositionFor(documentId: string): Disposition {
  return {
    id: 1,
    return_id: "ret_1",
    document_id: documentId,
    disposition: "entered_by_hand",
    note: "entered on Schedule C line 1 from the 1099-NEC",
    actor: "u_admin",
    at: "2026-10-09 10:00:00",
  };
}

/** The status the API answers after an action, from the fixture's preparing or in_review state. */
export function statusAfter(action: string): ReturnStatus {
  switch (action) {
    case "submit":
      return "in_review";
    case "approve":
      return "approved";
    case "request-changes":
      return "preparing";
    case "request-signature":
      return "awaiting_signature";
    case "release-approve":
      return "release_approved";
    case "reconcile":
      return "signed";
    default:
      return "preparing";
  }
}

/** A one-page PDF (the shape apps/web/e2e/fixtures/make-fixtures.mjs writes), for the inline document route. */
export const PDF_BYTES = new TextEncoder().encode(
  "%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n",
);
