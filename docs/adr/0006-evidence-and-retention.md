# ADR-0006: Evidence vault on R2 with client-side encryption and retention by record class

Status: Accepted. Spec references: §5 C04, §6 (fact provenance), §14 (records, retention), Q37.

## Decision

- **Encryption.** Every original is sealed with the firm's data key (AES-256-GCM) before it reaches R2. R2
  stores only ciphertext. Today's `Vault` class gets an R2 backend.
- **Keys.** Object keys are content-addressed: `firm/<firm>/obj/<sha256>`. Versions, classification,
  extracted fields, page coordinates and supersession links live in PostgreSQL (`SourceObject`,
  `DocumentVersion`, `ExtractedField`, `FactAssertion`).
- **Retention.** Each record class has a policy: original source, draft, finalized workpaper, signed
  authorization, accepted filing, disposable cache. Each policy maps to an R2 prefix with a bucket-lock
  rule for its minimum retention. Legal holds use an indefinite lock rule on a hold prefix plus a database
  hold record. Expiry runs a deletion job that skips held records and writes a deletion receipt.
- **No blanket WORM.** Retention follows the decision for each record class (spec §14).
- **Malware scanning** runs on intake before extraction. Rendered previews are generated in an isolated
  worker.

## Amendment 2026-10-09 (F-13): audit chain anchors, the lock probe and the restore drill

**Why.** Each firm's audit trail is a hash chain inside its own store. Whoever holds that store's owner credentials
can rewrite or truncate the chain and recompute every later hash, so the chain alone proves nothing against the
store's owner. The chain's head is therefore fixed outside the store, in an object the store's owner cannot change.

**Anchors.** `evidence/anchors.py` writes, per firm, `anchors/<firm>/<utc date>/<seq>.json` into the evidence bucket
(a top-level prefix next to `firms/<firm>/`, so one lock rule covers every firm and offboarding, which removes
`firms/<firm>/`, leaves the anchors alone; they hold hashes, never content). The object carries the audit head
(last seq, head hash, count), the head of every hashed workflow stream, the previous anchor's key and SHA-256
(anchors chain too), the platform build, the time, the lock probe's outcome, and an HMAC-SHA256 under a key derived
from the firm's data key (`Keyring.derive(firm, "audit-anchor")`): an anchor cannot be forged without the key and
becomes unverifiable when the firm is crypto-shredded. Keys are never reused or overwritten. An anchor is written
only when the chain moved since the last one (the anchor job's own `agent.run` records aside). The firm store keeps
an index, `audit_anchors` (SQLite schema; PostgreSQL migration 0008: write-once, only `verified_at` may change,
firm-wide RLS); the object store is the authority. Verification (`records.integrity`, `GET /api/evidence/integrity`,
field `anchors`) reads every anchor back, checks its signature and that its (seq, hash) is still in the chain, checks
the latest anchor's workflow stream heads, follows the previous-anchor links (a deleted or replaced anchor shows), and
reports `chain_rewritten` / `chain_truncated` / `workflow_rewritten` / `workflow_truncated` with the anchor that proves
it. A chain that contradicts its latest anchor is never anchored again. `anchor_missing` means unanchored activity
older than `AGENTLEDGER_ANCHOR_MAX_AGE_HOURS` (default 24) exists: the job did not run or failed.

**The lock.** R2 bucket locks (Cloudflare documentation, checked 2026-10-09:
<https://developers.cloudflare.com/r2/buckets/bucket-locks/>) "prevent the deletion and overwriting of objects in
an R2 bucket for a specified period, or indefinitely"; a rule selects objects by prefix (no prefix: the whole bucket)
and retains them for a number of days, until a date, or indefinitely; a bucket holds up to 1,000 rules, the strictest
retention wins where rules overlap, lock rules take precedence over lifecycle rules, rules apply to existing objects,
and a bucket with lock rules cannot be emptied. Rules are created in the dashboard (R2, the bucket, Settings, Bucket
lock rules), with Wrangler (`npx wrangler r2 bucket lock add <bucket> [NAME] [PREFIX] --retention-days N |
--retention-date YYYY-MM-DD | --retention-indefinite`; `lock list`, `lock remove --name`, `lock set --file`:
<https://developers.cloudflare.com/workers/wrangler/commands/r2/>) or with the API (`PUT
/accounts/{account_id}/r2/buckets/{bucket_name}/lock`, rules of `id`, `enabled`, `prefix`, `condition` of type `Age`
with `maxAgeSeconds`, `Date` with `date`, or `Indefinite`:
<https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/locks/>; announcement
<https://developers.cloudflare.com/changelog/post/2025-03-06-r2-bucket-locks/>). The rule for this platform: prefix
`anchors/`, indefinite (or at least 10 years, the longest statutory period the records support), one per bucket and
environment (docs/DEPLOY.md checklist 3b). The documentation does not state what error a delete of a locked object
returns, and a rule can be removed again by whoever holds the bucket's management rights, so the job does not trust
the configuration: at every run it writes a probe object under `anchors/<firm>/probe/` (one per UTC day, reused
while it exists) and tries to delete it; the anchors are reported locked only when the object is still there
afterwards. A delete that goes through (a bucket without the rule, the local S3 server in CI) records
`evidence.anchor_lock_missing` in the audit once per change of outcome and shows as `lock.status == "missing"` in
every anchor and report; anchors are still written. File blobs report `unchecked`. Only the first probe against the
real bucket with the rule in place proves the lock; the code can only refuse to claim it.

**Where the job runs.** The self-host profile runs it from the API scheduler as the `evidence-anchor` agent, hourly
per firm; `agentledger evidence anchor --all` is the same job as a command. On Cloudflare the containers start
without the scheduler thread, so a trigger inside the container (a Worker cron calling an internal endpoint) or an
operations job holding the master key and the R2 token is still to be chosen (backlog F-13, remaining).

**The restore drill.** `agentledger evidence restore-drill <firm>` (`evidence/drill.py`) restores the firm's store
into a scratch copy while the live store keeps running: SQLite through the backup API; PostgreSQL as a fresh schema
`drill_<token>` in the firm's own database, created from the current migrations with its own runtime role and loaded
from the firm's schema table by table in foreign-key order with the owner connection (user triggers off during the
load, identity sequences reset afterwards). It then verifies the copy as an operator would after a real restore: the
chain recomputes, every anchor in the object store still fits the copy, and every parked workflow instance
(`workflow_runs` running, sleeping or waiting) is present with its step results and would resume (LocalRunner lists
them from the copy; nothing is advanced). The drill is recorded in the live audit trail (`evidence.restore_drill`),
the report is written under the tenant's `drills/`, and the copy is dropped with its role unless kept for inspection.
What it does not yet prove: a restore from an actual backup (a Neon branch or point-in-time restore) rather than the
live schema.
