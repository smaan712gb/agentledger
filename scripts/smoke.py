"""Post-deploy smoke checks: health, published coverage, and one golden return computed through the API."""
import os
import sys

import httpx

base = sys.argv[1].rstrip("/")
h = {"Authorization": f"Bearer {os.environ['SMOKE_TOKEN']}"} if os.environ.get("SMOKE_TOKEN") else {}
ok = True
r = httpx.get(f"{base}/api/coverage", headers=h, timeout=30)
ok &= r.status_code == 200 and any(c["id"] == "f1040" for c in r.json()["capabilities"])
ret = {"tax_year": 2026, "filing_status": "single", "taxpayer": {"ssn": "400-00-0001", "dob": "1985-06-01"},
       "w2s": [{"wages": 60000, "federal_withholding": 6000}]}
r = httpx.post(f"{base}/api/returns/individual", json=ret, headers=h, timeout=60)
ok &= r.status_code == 200 and r.json()["forms"]["f1040"]["16"] == "5023"   # Rev. Proc. 2025-32 Tax Table value
print("smoke", "ok" if ok else "FAILED")
sys.exit(0 if ok else 1)
