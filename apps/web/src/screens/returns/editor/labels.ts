/**
 * Words for the generated editor: how the model's fields are grouped into sections, the labels of the fields that have
 * a box on a document (returns/model.py's comments, returns/documents.py BOXES), and the box labels the viewer shows
 * next to a document. Anything unnamed is humanised from its identifier, so a field the model gains is never hidden.
 */

export interface Section {
  id: string;
  title: string;
  fields: string[];
}

/** Top-level fields by section, in display order. Fields the plan does not name fall into "Other inputs". */
export const SECTIONS: Section[] = [
  {
    id: "people",
    title: "People",
    fields: [
      "tax_year",
      "filing_status",
      "taxpayer",
      "spouse",
      "mfs_lived_apart_all_year",
      "mfs_spouse_itemizes",
      "dependents",
    ],
  },
  { id: "wages", title: "Wages (Form W-2)", fields: ["w2s", "tips_form_4137", "tips_form_4137_occupation_code"] },
  { id: "interest", title: "Interest and dividends (1099-INT, 1099-DIV)", fields: ["interest", "dividends"] },
  {
    id: "investments",
    title: "Capital transactions (1099-B, Form 8949)",
    fields: ["capital_transactions", "capital_loss_carryover_short", "capital_loss_carryover_long"],
  },
  {
    id: "retirement",
    title: "Retirement and Social Security (1099-R, 5498, SSA-1099, Forms 8606 and 8880)",
    fields: [
      "retirement",
      "ira_accounts",
      "ira_facts",
      "retirement_savings",
      "retirement_savings_contributions",
      "social_security",
    ],
  },
  {
    id: "health",
    title: "Health (1099-SA, 5498-SA, 1095-A, Forms 8889 and 8962)",
    fields: [
      "hsa_distributions",
      "hsa_contributions",
      "hsa_facts",
      "marketplace_coverage",
      "excess_aptc_repayment",
      "net_premium_tax_credit",
    ],
  },
  {
    id: "other-income",
    title: "Other income (1099-G, Schedule 1)",
    fields: ["unemployment", "state_refund_taxable", "alimony_received", "other_income"],
  },
  {
    id: "business",
    title: "Business, rental and pass-through (Schedules C and E, K-1)",
    fields: [
      "businesses",
      "rentals",
      "k1s",
      "qbi_loss_carryforward",
      "reit_ptp_loss_carryforward",
      "household_employment_taxes",
    ],
  },
  { id: "prior-year", title: "Prior year", fields: ["prior_year"] },
  {
    id: "deductions",
    title: "Adjustments and deductions (Schedules 1 and A)",
    fields: [
      "adjustments",
      "itemized",
      "car_loans",
      "students",
      "dependent_care_expenses",
      "dependent_care_qualifying_persons",
    ],
  },
  {
    id: "credits",
    title: "Credits and elections",
    fields: [
      "foreign_tax_credit",
      "amt_adjustments",
      "claim_eic",
      "citizen_or_qualified_alien",
      "want_federal_public_benefit",
    ],
  },
  { id: "payments", title: "Payments", fields: ["payments"] },
];

export const OTHER_SECTION: Section = { id: "other", title: "Other inputs", fields: [] };

/** The sections with every root field placed: named ones where the plan says, the rest under "Other inputs". */
export function planSections(rootFields: string[]): Section[] {
  const placed = new Set(SECTIONS.flatMap((s) => s.fields));
  const sections = SECTIONS.map((s) => ({ ...s, fields: s.fields.filter((f) => rootFields.includes(f)) })).filter(
    (s) => s.fields.length > 0,
  );
  const rest = rootFields.filter((f) => !placed.has(f));
  return rest.length ? [...sections, { ...OTHER_SECTION, fields: rest }] : sections;
}

/** Model names as a preparer says them. */
export const MODEL_TITLES: Record<string, string> = {
  W2: "W-2",
  Interest: "1099-INT",
  Dividends: "1099-DIV",
  CapitalTransaction: "Capital transaction",
  Retirement: "1099-R",
  SocialSecurity: "SSA-1099",
  IRAAccount: "Form 5498",
  HSADistribution: "1099-SA",
  HSAContribution: "5498-SA",
  MarketplaceCoverage: "Form 1095-A",
  Unemployment: "1099-G",
  Business: "Schedule C business",
  Rental: "Schedule E rental",
  K1: "Schedule K-1",
  Dependent: "Dependent",
  Person: "Person",
  PriorYear: "Prior-year return",
  HSAFacts: "Form 8889 facts",
  IRAFacts: "IRA facts (Form 8606)",
  RetirementSavings: "Form 8880 facts",
  ForeignTaxCredit: "Form 1116 elections",
  ForeignTaxCarryover: "Foreign tax carryover",
  Section1231Loss: "Section 1231 loss",
  CarLoan: "Car loan",
  Student: "Student (Form 8863)",
  Itemized: "Itemized deductions (Schedule A)",
  Adjustments: "Adjustments (Schedule 1)",
  Payments: "Payments",
};

/** `Model.field` -> label; the box from the source document where there is one. */
export const FIELD_LABELS: Record<string, string> = {
  "IndividualReturn.tax_year": "Tax year",
  "IndividualReturn.filing_status": "Filing status",
  "IndividualReturn.taxpayer": "Taxpayer",
  "IndividualReturn.spouse": "Spouse",
  "IndividualReturn.w2s": "Forms W-2",
  "IndividualReturn.interest": "Forms 1099-INT",
  "IndividualReturn.dividends": "Forms 1099-DIV",
  "IndividualReturn.capital_transactions": "Capital transactions",
  "IndividualReturn.retirement": "Forms 1099-R",
  "IndividualReturn.ira_accounts": "Forms 5498",
  "IndividualReturn.hsa_distributions": "Forms 1099-SA",
  "IndividualReturn.hsa_contributions": "Forms 5498-SA",
  "IndividualReturn.marketplace_coverage": "Forms 1095-A",
  "IndividualReturn.social_security": "Forms SSA-1099",
  "IndividualReturn.unemployment": "Forms 1099-G",
  "IndividualReturn.k1s": "Schedules K-1",
  "IndividualReturn.other_income": "Other income (Schedule 1 line 8, by line letter)",
  "IndividualReturn.prior_year": "Prior-year return",
  "IndividualReturn.hsa_facts": "Form 8889 facts (per HSA beneficiary)",
  "IndividualReturn.ira_facts": "IRA Deduction Worksheet and Form 8606 facts (per person)",
  "IndividualReturn.retirement_savings": "Form 8880 facts (per person)",
  "IndividualReturn.retirement_savings_contributions": "Retirement savings contributions (deprecated hand total)",
  "IndividualReturn.foreign_tax_credit": "Form 1116 elections",
  "IndividualReturn.amt_adjustments": "AMT adjustments (Form 6251)",
  "IndividualReturn.claim_eic": "Claim the earned income credit",
  "IndividualReturn.tips_form_4137": "Qualified tips on Form 4137",
  "Person.ssn": "Social Security number",
  "Person.dob": "Date of birth",
  "Person.ssn_valid_for_employment": "SSN valid for employment",
  "Person.can_be_claimed_as_dependent": "Can be claimed as a dependent",
  "Person.full_time_student": "Full-time student",
  "Dependent.ssn": "SSN or TIN",
  "Dependent.tin_type": "TIN type",
  "Dependent.dob": "Date of birth",
  "W2.employer_name": "Employer (box c)",
  "W2.employer_ein": "Employer EIN (box b)",
  "W2.wages": "Wages, tips, other compensation (box 1)",
  "W2.federal_withholding": "Federal income tax withheld (box 2)",
  "W2.ss_wages": "Social security wages (box 3)",
  "W2.ss_tax": "Social security tax withheld (box 4)",
  "W2.medicare_wages": "Medicare wages and tips (box 5)",
  "W2.medicare_tax": "Medicare tax withheld (box 6)",
  "W2.ss_tips": "Social security tips (box 7)",
  "W2.dependent_care_benefits": "Dependent care benefits (box 10)",
  "W2.box12": "Box 12 codes and amounts",
  "W2.retirement_plan": "Retirement plan (box 13)",
  "W2.statutory_employee": "Statutory employee (box 13)",
  "W2.qualified_tips": "Qualified tips (box 12 code TP)",
  "W2.qualified_overtime": "Qualified overtime (box 12 code TT)",
  "W2.tipped_occupation_code": "Tipped occupation code (box 14b)",
  "W2.employer_sstb": "Employer is a specified service trade or business",
  "Interest.interest": "Interest income (box 1)",
  "Interest.early_withdrawal_penalty": "Early withdrawal penalty (box 2)",
  "Interest.us_savings_bond_interest": "Interest on U.S. savings bonds (box 3)",
  "Interest.federal_withholding": "Federal income tax withheld (box 4)",
  "Interest.foreign_tax_paid": "Foreign tax paid (box 6)",
  "Interest.foreign_country": "Foreign country or U.S. territory (box 7)",
  "Interest.tax_exempt_interest": "Tax-exempt interest (box 8)",
  "Interest.private_activity_bond_interest": "Specified private activity bond interest (box 9)",
  "Interest.foreign_source_income": "Foreign-source income (payer statement)",
  "Dividends.ordinary": "Total ordinary dividends (box 1a)",
  "Dividends.qualified": "Qualified dividends (box 1b)",
  "Dividends.capital_gain_distributions": "Total capital gain distributions (box 2a)",
  "Dividends.unrecaptured_1250_gain": "Unrecaptured section 1250 gain (box 2b)",
  "Dividends.section_1202_gain": "Section 1202 gain (box 2c)",
  "Dividends.collectibles_gain": "Collectibles (28%) gain (box 2d)",
  "Dividends.nondividend_distributions": "Nondividend distributions (box 3)",
  "Dividends.federal_withholding": "Federal income tax withheld (box 4)",
  "Dividends.section_199a_dividends": "Section 199A dividends (box 5)",
  "Dividends.foreign_tax_paid": "Foreign tax paid (box 7)",
  "Dividends.foreign_country": "Foreign country or U.S. territory (box 8)",
  "Dividends.exempt_interest_dividends": "Exempt-interest dividends (box 12)",
  "Dividends.private_activity_bond_dividends": "Specified private activity bond interest dividends (box 13)",
  "Retirement.gross_distribution": "Gross distribution (box 1)",
  "Retirement.taxable_amount": "Taxable amount (box 2a)",
  "Retirement.federal_withholding": "Federal income tax withheld (box 4)",
  "Retirement.distribution_code": "Distribution code (box 7)",
  "Retirement.ira_sep_simple": "IRA/SEP/SIMPLE",
  "SocialSecurity.net_benefits": "Net benefits (box 5)",
  "SocialSecurity.federal_withholding": "Federal income tax withheld (box 6)",
  "Unemployment.amount": "Unemployment compensation (box 1)",
  "Unemployment.federal_withholding": "Federal income tax withheld (box 4)",
  "IRAAccount.ira_contributions": "IRA contributions (box 1)",
  "IRAAccount.rollover_contributions": "Rollover contributions (box 2)",
  "IRAAccount.roth_conversion": "Roth IRA conversion amount (box 3)",
  "IRAAccount.recharacterized_contributions": "Recharacterized contributions (box 4)",
  "IRAAccount.fmv": "Fair market value of account (box 5)",
  "IRAAccount.account_type": "Account type (box 7)",
  "IRAAccount.sep_contributions": "SEP contributions (box 8)",
  "IRAAccount.simple_contributions": "SIMPLE contributions (box 9)",
  "IRAAccount.roth_contributions": "Roth IRA contributions (box 10)",
  "IRAAccount.rmd_required_next_year": "RMD required for next year (box 11)",
  "HSADistribution.gross_distribution": "Gross distribution (box 1)",
  "HSADistribution.earnings_on_excess": "Earnings on excess contributions (box 2)",
  "HSADistribution.distribution_code": "Distribution code (box 3)",
  "HSADistribution.fmv_on_date_of_death": "FMV on date of death (box 4)",
  "HSADistribution.account_type": "Account type (box 5)",
  "HSAContribution.archer_msa_contributions": "Archer MSA contributions (box 1)",
  "HSAContribution.total_contributions": "Total contributions made in the year (box 2)",
  "HSAContribution.following_year_contributions": "Contributions made in the following year for this year (box 3)",
  "HSAContribution.rollover_contributions": "Rollover contributions (box 4)",
  "HSAContribution.fmv": "Fair market value (box 5)",
  "HSAContribution.account_type": "Account type (box 6)",
  "MarketplaceCoverage.annual_premium": "Annual enrollment premiums (line 33, column A)",
  "MarketplaceCoverage.annual_slcsp": "Annual SLCSP premium (line 33, column B)",
  "MarketplaceCoverage.annual_aptc": "Annual advance payment of the premium tax credit (line 33, column C)",
  "PriorYear.tax": "Total tax (prior-year Form 1040 line 24)",
  "PriorYear.agi": "Adjusted gross income (prior-year Form 1040 line 11)",
  "PriorYear.capital_loss_carryover_short": "Short-term capital loss carryover (worksheet line 8)",
  "PriorYear.capital_loss_carryover_long": "Long-term capital loss carryover (worksheet line 13)",
  "Itemized.mortgage_interest_1098": "Mortgage interest on Form 1098 (box 1)",
  "Itemized.mortgage_insurance_premiums": "Mortgage insurance premiums (Form 1098 box 5)",
  "Adjustments.student_loan_interest_paid": "Student loan interest paid (Form 1098-E box 1)",
  "Adjustments.hsa_deduction": "HSA deduction (deprecated: computed from Form 8889)",
  "Adjustments.ira_deduction": "IRA deduction (deprecated: computed from the worksheet)",
};

/** Box labels per input list, for the viewer and the provenance chips (returns/documents.py BOXES). */
export const BOX_LABELS: Record<string, Record<string, string>> = {
  w2s: {
    box1: "Box 1 — Wages, tips, other compensation",
    box2: "Box 2 — Federal income tax withheld",
    box3: "Box 3 — Social security wages",
    box4: "Box 4 — Social security tax withheld",
    box5: "Box 5 — Medicare wages and tips",
    box6: "Box 6 — Medicare tax withheld",
    box7: "Box 7 — Social security tips",
    box10: "Box 10 — Dependent care benefits",
    box14b: "Box 14b — Tipped occupation code",
    recipient: "Employee (box e)",
  },
  interest: {
    box1: "Box 1 — Interest income",
    box2: "Box 2 — Early withdrawal penalty",
    box3: "Box 3 — Interest on U.S. savings bonds and Treasury obligations",
    box4: "Box 4 — Federal income tax withheld",
    box6: "Box 6 — Foreign tax paid",
    box7: "Box 7 — Foreign country or U.S. territory",
    box8: "Box 8 — Tax-exempt interest",
    box9: "Box 9 — Specified private activity bond interest",
    recipient: "Recipient",
  },
  dividends: {
    box1a: "Box 1a — Total ordinary dividends",
    box1b: "Box 1b — Qualified dividends",
    box2a: "Box 2a — Total capital gain distributions",
    box2b: "Box 2b — Unrecaptured section 1250 gain",
    box2c: "Box 2c — Section 1202 gain",
    box2d: "Box 2d — Collectibles (28%) gain",
    box3: "Box 3 — Nondividend distributions",
    box4: "Box 4 — Federal income tax withheld",
    box5: "Box 5 — Section 199A dividends",
    box7: "Box 7 — Foreign tax paid",
    box8: "Box 8 — Foreign country or U.S. territory",
    box12: "Box 12 — Exempt-interest dividends",
    box13: "Box 13 — Specified private activity bond interest dividends",
    recipient: "Recipient",
  },
  retirement: {
    box1: "Box 1 — Gross distribution",
    box2a: "Box 2a — Taxable amount",
    box4: "Box 4 — Federal income tax withheld",
    box7: "Box 7 — Distribution code",
    recipient: "Recipient",
  },
  social_security: {
    box5: "Box 5 — Net benefits",
    box6: "Box 6 — Federal income tax withheld",
    recipient: "Beneficiary",
  },
  unemployment: { box1: "Box 1 — Unemployment compensation", box4: "Box 4 — Federal income tax withheld" },
  hsa_distributions: {
    box1: "Box 1 — Gross distribution",
    box2: "Box 2 — Earnings on excess contributions",
    box3: "Box 3 — Distribution code",
    box4: "Box 4 — FMV on date of death",
    box5: "Box 5 — Account type",
  },
  hsa_contributions: {
    box1: "Box 1 — Archer MSA contributions",
    box2: "Box 2 — Total contributions made in the year",
    box3: "Box 3 — Total contributions made in the following year for this year",
    box4: "Box 4 — Rollover contributions",
    box5: "Box 5 — Fair market value",
    box6: "Box 6 — Account type",
  },
  ira_accounts: {
    box1: "Box 1 — IRA contributions",
    box2: "Box 2 — Rollover contributions",
    box3: "Box 3 — Roth IRA conversion amount",
    box4: "Box 4 — Recharacterized contributions",
    box5: "Box 5 — Fair market value of account",
    box7: "Box 7 — Account type",
    box8: "Box 8 — SEP contributions",
    box9: "Box 9 — SIMPLE contributions",
    box10: "Box 10 — Roth IRA contributions",
    box11: "Box 11 — RMD required for next year",
  },
  marketplace_coverage: {
    annual_premium: "Line 33, column A — Annual enrollment premiums",
    annual_slcsp: "Line 33, column B — Annual SLCSP premium",
    annual_aptc: "Line 33, column C — Annual advance payment of the premium tax credit",
  },
  itemized: {
    box1: "Form 1098 box 1 — Mortgage interest received",
    box5: "Form 1098 box 5 — Mortgage insurance premiums",
  },
  adjustments: { box1: "Form 1098-E box 1 — Student loan interest received" },
};

/** "medicare_wages" -> "Medicare wages"; "box12" -> "Box 12". */
export function humanise(identifier: string): string {
  const words = identifier.replace(/_/g, " ").replace(/^box(\d)/, "box $1");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function fieldLabel(model: string, field: string): string {
  return FIELD_LABELS[`${model}.${field}`] ?? humanise(field);
}

export function modelTitle(model: string): string {
  return MODEL_TITLES[model] ?? humanise(model);
}

/** The label of a document box as the viewer shows it; a code inside box 12 ("box12 D") names the code. */
export function boxLabel(list: string | null, box: string | null | undefined): string {
  if (!box) return "the document";
  const m = /^box12[ .](\w+)$/.exec(box);
  if (m) return `Box 12 — code ${m[1] ?? ""}`;
  return (list ? BOX_LABELS[list]?.[box] : undefined) ?? humanise(box);
}

/** The title of a list item: the model, its position, and the name the document printed. */
export function itemTitle(model: string, index: number, item: Record<string, unknown>): string {
  const name = ["employer_name", "payer", "trustee", "issuer", "entity_name", "name", "description", "address"]
    .map((k) => item[k])
    .find((v): v is string => typeof v === "string" && v.trim() !== "");
  return `${modelTitle(model)} ${index + 1}${name ? ` · ${name}` : ""}`;
}
