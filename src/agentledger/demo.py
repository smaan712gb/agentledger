"""Seed a realistic multi-industry demo firm (idempotent: refuses to run twice)."""

from __future__ import annotations

import shutil
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from . import audit
from .app_context import AppContext
from .brain.playbooks import add_precedent
from .calc.federal import Asset
from .crm import business, core as crm
from .domains.service import onboard, post_template
from .integrity.checks import run_all
from .ledger import store
from .ledger.store import Line


def seed(root: Path) -> AppContext:
    app = AppContext.open(root)
    conn, kb, packs = app.conn, app.kb, app.packs
    if store.list_clients(conn):
        raise SystemExit("demo data already present (delete state/agentledger.db to reseed)")
    consent = "2026-01-15T10:00:00+00:00"
    y = date.today().year

    def booked(on: date):
        return audit.clock(datetime.combine(on + timedelta(days=2), time(17), tzinfo=timezone.utc))

    def tpl(cid: str, t: str, on: date, **inputs) -> int:
        with booked(on):
            return post_template(conn, packs, kb, cid, t, inputs, on, actor="maya.chen", role="cpa")

    def post(cid: str, on: date, memo: str, lines: list, **kw) -> int:
        with booked(on):
            return store.post(conn, cid, on, memo, lines, **kw)

    # -- Ortiz Auto Care: auto repair S corp ---------------------------------------------------
    store.add_client(conn, id="ortiz-auto", name="Ortiz Auto Care LLC", kind="business", entity_type="s_corp", tax_id_last4="4471",
                     emails=["sam@ortizauto.example"], aliases=["Ortiz Auto"], consent_7216_at=consent, domain="auto_repair",
                     facts={"state": "TX", "employees": 6, "technicians": 4, "billed_hours": 3100, "owner_has_home": True,
                            "contractor_payments": True, "has_retirement_plan": False, "state_has_ptet": False}, actor="maya.chen")
    onboard(conn, packs, "ortiz-auto", "auto_repair")
    post("ortiz-auto", date(y - 1, 1, 1), "Opening balances", [Line("1000", Decimal("48000")), Line("3000", Decimal("-48000"))],
               source="opening", actor="maya.chen")
    for m in range(1, 13):
        tpl("ortiz-auto", "repair_order", date(y - 1, m, 15), ro=f"9{m:03d}", vehicle="monthly batch", parts=21000, labor=17500, sublet=1200,
            core_charge=600, sales_tax=1840)
        tpl("ortiz-auto", "supplier_invoice_with_core", date(y - 1, m, 10), vendor="NAPA", invoice=f"N{m}", base_cost=12600, core_amount=600)
        post("ortiz-auto", date(y - 1, m, 28), "Technician flat-rate pay", [Line("6010", Decimal("9800")), Line("1000", Decimal("-9800"))],
                   source="payroll", actor="maya.chen")
        post("ortiz-auto", date(y - 1, m, 1), "Shop rent", [Line("6100", Decimal("4200")), Line("1000", Decimal("-4200"))],
                   source="bank:RENT", actor="maya.chen")
    for m in range(1, 9):
        tpl("ortiz-auto", "repair_order", date(y, m, 15), ro=f"10{m:03d}", vehicle="monthly batch", parts=23500, labor=19800, sublet=900,
            core_charge=650, sales_tax=2050)
        tpl("ortiz-auto", "supplier_invoice_with_core", date(y, m, 10), vendor="NAPA", invoice=f"N{y}{m}", base_cost=13900, core_amount=650)
        post("ortiz-auto", date(y, m, 1), "Shop rent", [Line("6100", Decimal("4400")), Line("1000", Decimal("-4400"))],
                   source="bank:RENT", actor="maya.chen")
    tpl("ortiz-auto", "business_meal", date(y, 5, 14), attendees="City Fleet purchasing manager", purpose="fleet maintenance contract renewal",
        amount=240)
    post("ortiz-auto", date(y, 6, 7), "Golf outing with City Fleet managers",
               [Line("6200", Decimal("1150"), "meals"), Line("2100", Decimal("-1150"))], source="card", actor="sam.ortiz", role="client")
    post("ortiz-auto", date(y, 7, 2), "Core charge RO 10611 (recorded as parts sale)",
               [Line("1000", Decimal("95")), Line("4010", Decimal("-95"))], source="pos", actor="sam.ortiz", role="client")
    post("ortiz-auto", date(y, 4, 3), "City of Austin parking citation for tow truck",
               [Line("6700", Decimal("180")), Line("1000", Decimal("-180"))], source="card", actor="sam.ortiz", role="client")
    store.add_asset(conn, "ortiz-auto", Asset("lift-01", Decimal("68000"), date(y, 3, 2), date(y, 3, 10), 7, 10, Decimal("68000")),
                    "Two-post vehicle lift")
    store.add_asset(conn, "ortiz-auto", Asset("scanner-01", Decimal("14500"), date(y, 2, 1), date(y, 2, 5), 5, 5), "Diagnostic scan tool")
    tpl("ortiz-auto", "book_depreciation", date(y, 8, 31), period=f"{y} YTD", amount=Decimal("68000") / 10 / 2 + Decimal("14500") / 5 / 2)
    fleet = business.add_party(conn, "ortiz-auto", "customer", "City Fleet Services", email="ap@cityfleet.example", terms_days=30,
                               actor="sam.ortiz")
    business.add_party(conn, "ortiz-auto", "vendor", "NAPA Auto Parts", entity_type="c_corp", w9_on_file=True, actor="sam.ortiz")
    trans = business.add_party(conn, "ortiz-auto", "vendor", "Precision Transmissions LLC", entity_type="llc", actor="sam.ortiz")
    for d, amt in ((date(y, 2, 20), 1400), (date(y, 6, 11), 2100)):
        with booked(d):
            business.pay_vendor(conn, "ortiz-auto", trans, amt, "5030", "transmission rebuild sublet", on=d, actor="sam.ortiz")
    with booked(date(y, 6, 20)):
        business.create_invoice(conn, "ortiz-auto", fleet, "F-2207", 8640, "Fleet PM service, 12 vans", issued=date(y, 6, 20), actor="sam.ortiz")
    with booked(date(y, 8, 25)):
        business.create_invoice(conn, "ortiz-auto", fleet, "F-2291", 5230, "Brake jobs, 4 vans", issued=date(y, 8, 25), actor="sam.ortiz")
    business.add_deal(conn, "ortiz-auto", "City Fleet 2027 maintenance contract", 96000, fleet, "proposal", f"{y}-11-30", actor="sam.ortiz")
    business.add_deal(conn, "ortiz-auto", "School district bus inspections", 22000, None, "qualified", f"{y + 1}-01-31", actor="sam.ortiz")
    crm.add_contact(conn, "ortiz-auto", "Sam Ortiz", "sam@ortizauto.example", role="Owner", primary=True)
    crm.add_engagement(conn, "ortiz-auto", "1120-S return", y - 1, "maya.chen", f"{y}-09-15", "4800", "in_progress", actor="maya.chen")

    # -- Lakeside Fuel & Market: gas station partnership -----------------------------------------
    store.add_client(conn, id="lakeside-fuel", name="Lakeside Fuel & Market LLC", kind="business", entity_type="partnership",
                     tax_id_last4="9022", emails=["priya@lakesidefuel.example"], domain="gas_station",
                     facts={"state": "MI", "employees": 9, "gallons_sold": 1250000, "excise_registrant": False, "contractor_payments": True,
                            "has_retirement_plan": True, "state_has_ptet": True, "owner_has_home": True}, actor="maya.chen")
    onboard(conn, packs, "lakeside-fuel", "gas_station")
    post("lakeside-fuel", date(y, 1, 1), "Opening balances", [Line("1000", Decimal("150000")), Line("1210", Decimal("60000")),
               Line("3000", Decimal("-210000"))], source="opening", actor="maya.chen")
    for m in range(1, 9):
        tpl("lakeside-fuel", "daily_fuel_sales", date(y, m, 28), day=f"{y}-{m:02d} total", gross_receipts=412000, state_tax_collected=41500)
        post("lakeside-fuel", date(y, m, 28), "Fuel cost of sales", [Line("5100", Decimal("330000")), Line("1210", Decimal("-330000"))],
                   source="inventory", actor="maya.chen")
        post("lakeside-fuel", date(y, m, 25), "Fuel deliveries (bills of lading)", [Line("1210", Decimal("335000")),
                   Line("2000", Decimal("-335000"))], source="ap", actor="maya.chen")
        tpl("lakeside-fuel", "daily_lottery", date(y, m, 28), day=f"{y}-{m:02d} total", ticket_sales=38000, prize_payouts=11000, commission=1900)
        tpl("lakeside-fuel", "fuel_shrink", date(y, m, 27), tank="T1-T3", book_gallons=110000, measured_gallons=109400, cost_per_gallon=2.95)
    post("lakeside-fuel", date(y, 2, 15), "Lottery scratch ticket sales (register 2)",
               [Line("1000", Decimal("6200")), Line("4200", Decimal("-6200"))], source="pos", actor="priya.n", role="client",
               created_at=f"{y}-09-30T16:20:00+00:00")
    crm.add_contact(conn, "lakeside-fuel", "Priya Natarajan", "priya@lakesidefuel.example", role="Managing member", primary=True)
    crm.add_engagement(conn, "lakeside-fuel", "1065 return + monthly bookkeeping", y, "maya.chen", f"{y + 1}-03-15", "14400", "engaged",
                       actor="maya.chen")

    # -- Jordan Lee: individual ------------------------------------------------------------------
    store.add_client(conn, id="jordan-lee", name="Jordan Lee", kind="individual", tax_id_last4="5308",
                     emails=["jordan.lee@example.com"], consent_7216_at=consent, domain="general",
                     facts={"state": "PA", "has_tip_income": True, "has_overtime": False, "employees": 0}, actor="maya.chen")
    onboard(conn, packs, "jordan-lee", "general")
    post("jordan-lee", date(y - 1, 3, 31), "Freelance design income Q1", [Line("1000", Decimal("4100")), Line("4000", Decimal("-4100"))],
               source="bank:DEPOSIT", actor="jordan.lee", role="client")
    conn.execute("INSERT INTO info_returns (client_id, document_id, form, tax_year, payer, amount) VALUES (?,?,?,?,?,?)",
                 ("jordan-lee", None, "1099-NEC", y - 1, "Brightline Consulting LLC", "10300.00"))
    crm.add_engagement(conn, "jordan-lee", "1040 return", y - 1, "maya.chen", f"{y}-10-15", "650", "client_review", actor="maya.chen")

    # -- Summit Ridge Fund II: private equity ----------------------------------------------------
    store.add_client(conn, id="summit-ridge", name="Summit Ridge Capital Fund II LP", kind="business", entity_type="partnership",
                     formed_under="domestic", tax_id_last4="1186", emails=["fundops@summitridge.example"], consent_7216_at=consent,
                     domain="pe_fund", facts={"committed_capital": 250000000, "vintage_year": 2024, "investors": 41, "state": "DE",
                                              "state_of_formation": "DE", "employees": 0}, actor="maya.chen")
    onboard(conn, packs, "summit-ridge", "pe_fund")
    tpl("summit-ridge", "capital_call", date(y, 1, 15), call_no="5", lp_amount=29700000, gp_amount=300000)
    tpl("summit-ridge", "capital_call_funded", date(y, 1, 30), call_no="5", amount=30000000)
    tpl("summit-ridge", "investment_purchase", date(y, 2, 12), company="Northwind Logistics", cost=24000000)
    tpl("summit-ridge", "management_fee", date(y, 3, 31), period=f"{y}-Q1", committed_capital=250000000, annual_rate=0.02, months=3)
    tpl("summit-ridge", "management_fee", date(y, 6, 30), period=f"{y}-Q2", committed_capital=250000000, annual_rate=0.02, months=3)
    tpl("summit-ridge", "fair_value_mark", date(y, 6, 30), company="Northwind Logistics", period=f"{y}-Q2", change=3100000)

    add_precedent(conn, topic="meals with entertainment", situation="Client booked a golf outing with customers as a business meal.",
                  judgment="Golf is entertainment under §274(a) even when business is discussed; only separately stated food and "
                           "beverage on a separate invoice can be a 50% meal. Reclassify and get the itemized bill.",
                  citations=["IRC §274(a)", "Treas. Reg. §1.274-11(b)"], author="maya.chen")

    audit.record(conn, "maya.chen", "cpa", "demo.seeded", {"clients": 4})
    for cid in ("ortiz-auto", "lakeside-fuel", "jordan-lee", "summit-ridge"):
        for yr in (y - 1, y):
            run_all(conn, kb, cid, yr, packs=packs)

    samples = Path(__file__).resolve().parent / "samples"
    (root / "maildrop").mkdir(exist_ok=True)
    for f in samples.glob("*"):
        shutil.copy(f, root / "maildrop" / f.name)
    return app
