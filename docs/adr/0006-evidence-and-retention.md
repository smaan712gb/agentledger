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
