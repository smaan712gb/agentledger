-- Audit chain anchors (backlog F-13, ADR-0006 amendment): the firm store's index of the anchor objects written to its
-- object store (evidence/anchors.py). The object store (R2 under a bucket lock rule on anchors/) is the authority;
-- this table makes "did the head move since the last anchor" and "when was it last verified" one lookup. Same
-- columns as the SQLite schema (db.py). A row is written once; only verified_at changes afterwards (like a legal
-- hold's release columns, 0003).
CREATE TABLE audit_anchors (
    object_key    text PRIMARY KEY,
    seq           bigint NOT NULL UNIQUE,
    head_hash     text NOT NULL,
    object_sha256 text NOT NULL,
    written_at    text NOT NULL,
    lock_status   text NOT NULL CHECK (lock_status IN ('locked', 'missing', 'unchecked')),
    verified_at   text
);

CREATE FUNCTION audit_anchor_verify_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'audit anchors are never deleted' USING ERRCODE = 'AL006';
    END IF;
    IF (NEW.object_key, NEW.seq, NEW.head_hash, NEW.object_sha256, NEW.written_at, NEW.lock_status)
           IS DISTINCT FROM (OLD.object_key, OLD.seq, OLD.head_hash, OLD.object_sha256, OLD.written_at, OLD.lock_status) THEN
        RAISE EXCEPTION 'an audit anchor only records its verification' USING ERRCODE = 'AL006';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER audit_anchors_verify_only BEFORE UPDATE OR DELETE ON audit_anchors FOR EACH ROW EXECUTE FUNCTION audit_anchor_verify_only();
CREATE TRIGGER audit_anchors_no_truncate BEFORE TRUNCATE ON audit_anchors FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- Firm data: visible and writable to firm-wide sessions only, like kv and workflow_runs.
ALTER TABLE audit_anchors ENABLE ROW LEVEL SECURITY;
CREATE POLICY scope_audit_anchors ON audit_anchors USING (firm_wide()) WITH CHECK (firm_wide());
GRANT SELECT, INSERT ON audit_anchors TO {{app_role}};
GRANT UPDATE (verified_at) ON audit_anchors TO {{app_role}};
