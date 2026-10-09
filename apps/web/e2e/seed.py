"""Seeds the end-to-end platform. Run by e2e/servers.mjs, with the API's environment, before the API starts:

    python apps/web/e2e/seed.py <home>/e2e-seed.json

Creates the first platform administrator (two-step verification enrolled), one firm, and the firm accounts the
specs sign in with; every spec has its own accounts because a one-time code cannot be reused within its 30-second
step. Writes each account's email, TOTP secret and the step the seed itself used to the JSON file for the specs.

This is the recipe of tests/test_tenancy.py (bootstrap admin -> login + TOTP -> create firm -> invite -> accept
-> enrol), done through the Platform class so no server is needed. The password is public and the data lives in a
temporary directory that the next run deletes: nothing here is ever used outside the e2e run.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from agentledger.security import totp
from agentledger.security.platform import Platform

PASSWORD = "correct horse battery staple"  # e2e only (tests/test_tenancy.py uses the same phrase)
FIRM_ID = "rivera-cpa"
FIRM_NAME = "Rivera CPA"

# key -> (email, role, name, enrolled). One account per spec; the sign-in spec has three of its own.
MEMBERS = [
    ("maya", "maya@rivera.example", "firm_admin", "Maya Rivera", True),      # invite.spec
    ("noor", "noor@rivera.example", "firm_admin", "Noor Haddad", True),      # clients.spec
    ("lee", "lee@rivera.example", "cpa", "Lee Park", True),                  # documents.spec
    ("kai", "kai@rivera.example", "cpa", "Kai Chen", True),                  # keyboard.spec
    ("dana", "dana@rivera.example", "cpa", "Dana Flores", True),             # fragments.spec
    # signin.spec runs in two Playwright projects (desktop, mobile); each project gets its own accounts because an
    # enrolment happens once and a lockout lasts 15 minutes.
    ("sora", "sora@rivera.example", "staff", "Sora Ito", True),              # sign-out
    ("newbie", "newbie@rivera.example", "cpa", "Newbie Nguyen", False),      # enrolment
    ("wrong", "wrong@rivera.example", "cpa", "Wrong Code", True),            # wrong code
    ("lock", "lock@rivera.example", "cpa", "Lock Out", True),                # lockout
    ("sora_mobile", "sora.mobile@rivera.example", "staff", "Sora Ito (mobile)", True),
    ("newbie_mobile", "newbie.mobile@rivera.example", "cpa", "Newbie Nguyen (mobile)", False),
    ("wrong_mobile", "wrong.mobile@rivera.example", "cpa", "Wrong Code (mobile)", True),
    ("lock_mobile", "lock.mobile@rivera.example", "cpa", "Lock Out (mobile)", True),
]


def current_step() -> int:
    return int(time.time() // totp.STEP)


def main(out: Path) -> None:
    home = Path(os.environ["AGENTLEDGER_HOME"])
    plat = Platform(home, dev=False, identity="local")
    users: dict[str, dict[str, object]] = {}
    try:
        ops_id = plat.bootstrap_admin("ops@agentledger.example", "Ops Admin", PASSWORD)
        step = plat.login("ops@agentledger.example", PASSWORD)
        enrolment = step["enroll"]
        used = current_step()
        plat.complete_mfa(enrolment.challenge, totp.code_at(enrolment.secret, used))
        users["ops"] = {"email": "ops@agentledger.example", "name": "Ops Admin", "role": "platform_admin",
                        "secret": enrolment.secret, "last_step": used}
        by = {"id": ops_id, "role": "platform_admin", "firm_id": "_platform"}

        firm = plat.create_firm(FIRM_ID, FIRM_NAME, by=ops_id)
        if firm["status"] != "active":
            raise SystemExit(f"the firm is {firm['status']}; the e2e run needs SQLite stores (status active at once)")

        for key, email, role, name, enrolled in MEMBERS:
            token = plat.invite(FIRM_ID, email, role, by=by)
            enrolment = plat.accept_invite(token, name, PASSWORD)
            record: dict[str, object] = {"email": email, "name": name, "role": role, "secret": None, "last_step": None}
            if enrolled:
                used = current_step()
                plat.complete_mfa(enrolment.challenge, totp.code_at(enrolment.secret, used))
                record["secret"] = enrolment.secret
                record["last_step"] = used
            users[key] = record
    finally:
        plat.close()

    out.write_text(json.dumps({"password": PASSWORD, "firm": FIRM_ID, "firm_name": FIRM_NAME, "users": users}, indent=2),
                   encoding="utf-8")
    print(f"[seed] {len(users)} accounts in firm {FIRM_ID} -> {out}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: seed.py <output.json>")
    main(Path(sys.argv[1]))
