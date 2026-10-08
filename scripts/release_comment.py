"""Turn `veritas release classify` output (decision.json) into a pull-request comment."""
import json
import sys

d = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "decision.json", encoding="utf-8"))
lines = [f"**Release decision:** {d['verdict']}", f"**Needs approval by:** {d.get('approver') or 'n/a'}", ""]
lines += [f"- {r}" for r in d.get("reasons", [])]
print("\n".join(lines))
