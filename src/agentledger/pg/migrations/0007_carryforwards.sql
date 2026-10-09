-- T1-01 S1b: what a return version carries to the next tax year (Result.carryforwards: capital loss carryovers, IRA
-- basis, the HSA last-month-rule amount), one row per version, kind and detail (the owner for a per-person kind, else
-- empty). The amount is sealed with the firm key like the version it belongs to; kind and detail are in clear so the
-- roll-forward into the next year's return can find them. Append-only, visible exactly when the return is. Same columns
-- as the SQLite schema (returns/store.py).
CREATE TABLE return_carryforwards (
    return_id text NOT NULL REFERENCES tax_returns(id),
    version   int NOT NULL,
    kind      text NOT NULL,
    detail    text NOT NULL DEFAULT '',
    amount    text NOT NULL,
    at        text NOT NULL,
    PRIMARY KEY (return_id, version, kind, detail)
);
CREATE TRIGGER return_carryforwards_immutable BEFORE UPDATE OR DELETE ON return_carryforwards
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER return_carryforwards_no_truncate BEFORE TRUNCATE ON return_carryforwards
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
ALTER TABLE return_carryforwards ENABLE ROW LEVEL SECURITY;
CREATE POLICY scope_return_carryforwards ON return_carryforwards
    USING (EXISTS (SELECT 1 FROM tax_returns t WHERE t.id = return_carryforwards.return_id))
    WITH CHECK (EXISTS (SELECT 1 FROM tax_returns t WHERE t.id = return_carryforwards.return_id));
GRANT SELECT, INSERT ON return_carryforwards TO {{app_role}};
