-- Workflows adapter and the return filing state machine (backlog F-08, ADR-0003). Same columns and value formats as
-- the SQLite schemas (db.py, returns/store.py), so every module runs unchanged on either backend.

-- ------------------------------------------------------------------------------------------------- outbox
-- Domain events for orchestration are written by the application in the transaction of the change they describe.
-- The application role keeps SELECT only on `outbox` (0001): it inserts through this function, which checks that the
-- client is in the session's scope, and reads the delivery marks `mark_delivered` (0001) writes.
CREATE FUNCTION emit_event(p_client text, p_event_type text, p_aggregate text, p_aggregate_id text, p_payload jsonb)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE
    v_id bigint;
BEGIN
    PERFORM assert_scope(p_client);
    INSERT INTO outbox (client_id, event_type, aggregate, aggregate_id, payload)
    VALUES (p_client, p_event_type, p_aggregate, p_aggregate_id, p_payload) RETURNING id INTO v_id;
    RETURN v_id;
END $$;
GRANT EXECUTE ON FUNCTION emit_event TO {{app_role}};
GRANT SELECT ON outbox_delivery TO {{app_role}};

-- ------------------------------------------------------------------------------------------------- local runner
-- Orchestration state of the in-process workflow runner (workflow/runner.py; the self-host profile and tests): which
-- step an instance is on, its timers and waits. Firm data, never business state: the domain never reads it.
CREATE TABLE workflow_runs (
    instance_id text PRIMARY KEY,
    flow        text NOT NULL,
    params      text NOT NULL,
    steps       text NOT NULL DEFAULT '{}',
    status      text NOT NULL CHECK (status IN ('running', 'sleeping', 'waiting', 'complete', 'errored')),
    wake_at     text,
    waiting_for text,
    events      text NOT NULL DEFAULT '[]',
    result      text,
    error       text,
    created_at  text NOT NULL,
    updated_at  text NOT NULL
);
ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY scope_workflow_runs ON workflow_runs USING (firm_wide()) WITH CHECK (firm_wide());
GRANT SELECT, INSERT, UPDATE ON workflow_runs TO {{app_role}};

-- ------------------------------------------------------------------------------------------------- submissions
-- One row per electronic submission of a return to one jurisdiction (US-FED, US-XX), original or retransmission.
-- The row mirrors the submission's own event stream (workflow_events keyed by the submission id), which is the
-- record; a state submission is linked to the federal one it may only follow.
CREATE TABLE filing_submissions (
    id                     text PRIMARY KEY,
    return_id              text NOT NULL REFERENCES tax_returns(id),
    jurisdiction           text NOT NULL,
    kind                   text NOT NULL CHECK (kind IN ('original', 'retransmission')),
    supersedes             text REFERENCES filing_submissions(id),
    linked_to              text REFERENCES filing_submissions(id),
    package_hash           text NOT NULL,
    attempt                int NOT NULL DEFAULT 1,
    planned_submission_id  text,
    provider_submission_id text,
    status                 text NOT NULL,
    ack_payload            text,
    rejection_codes        text NOT NULL DEFAULT '[]',
    created_by             text NOT NULL,
    created_at             text NOT NULL,
    updated_at             text NOT NULL
);
CREATE INDEX filing_submissions_return ON filing_submissions (return_id, jurisdiction);
CREATE UNIQUE INDEX filing_submissions_planned ON filing_submissions (planned_submission_id) WHERE planned_submission_id IS NOT NULL;
ALTER TABLE filing_submissions ENABLE ROW LEVEL SECURITY;
CREATE POLICY scope_filing_submissions ON filing_submissions
    USING (EXISTS (SELECT 1 FROM tax_returns t WHERE t.id = filing_submissions.return_id))
    WITH CHECK (EXISTS (SELECT 1 FROM tax_returns t WHERE t.id = filing_submissions.return_id));
GRANT SELECT, INSERT, UPDATE ON filing_submissions TO {{app_role}};

-- A workflow stream is a return's (0002) or a submission's: either way visible and writable only when the return is.
DROP POLICY scope_workflow_events ON workflow_events;
CREATE POLICY scope_workflow_events ON workflow_events
    USING (EXISTS (SELECT 1 FROM tax_returns t WHERE t.id = workflow_events.workflow_id)
           OR EXISTS (SELECT 1 FROM filing_submissions s WHERE s.id = workflow_events.workflow_id))
    WITH CHECK (EXISTS (SELECT 1 FROM tax_returns t WHERE t.id = workflow_events.workflow_id)
                OR EXISTS (SELECT 1 FROM filing_submissions s WHERE s.id = workflow_events.workflow_id));
