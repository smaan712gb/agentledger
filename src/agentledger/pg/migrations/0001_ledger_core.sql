-- AgentLedger financial core on PostgreSQL (ADR-0002). One authoritative ledger; invariants enforced here, not only
-- in application code:
--   * the application role reads tables but never writes them: every write goes through SECURITY DEFINER functions
--   * a journal must have >= 2 lines and balance (deferred constraint trigger, checked at COMMIT, on every route)
--   * posted entries, postings, receipts, outbox events and audit records are immutable
--   * closed periods reject entries (trigger), with a reasoned, audited, CPA-only reopen
--   * one financial effect per command id (receipts with payload-hash conflict detection)
--   * each posting writes its outbox event and audit record in the same transaction
--   * per-client hash chains detect tampering below the application
--   * row-level security scopes reads to the clients set for the session (defense in depth inside a firm database)
-- The runner executes this file with search_path set to "<schema>, public", so functions pin it via FROM CURRENT.
-- Error codes: AL001 unbalanced, AL002 closed period, AL003 command conflict, AL004 out of scope,
--              AL005 invalid request, AL006 append-only, AL007 not authorized.

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agentledger_app') THEN
        CREATE ROLE agentledger_app NOLOGIN NOBYPASSRLS;
    END IF;
    -- The migrating (owner) role may switch to the app role for requests; it never inherits its privileges.
    EXECUTE format('GRANT agentledger_app TO %I WITH INHERIT FALSE, SET TRUE', current_user);
END $$;

-- ------------------------------------------------------------------------------------------------- tables
CREATE TABLE clients (
    id              text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    name            text NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('business', 'individual')),
    functional_currency char(3) NOT NULL DEFAULT 'USD',
    closed_through  date,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    client_id   text NOT NULL REFERENCES clients(id),
    code        text NOT NULL,
    name        text NOT NULL,
    type        text NOT NULL CHECK (type IN ('asset', 'liability', 'equity', 'revenue', 'expense')),
    PRIMARY KEY (client_id, code)
);

CREATE TABLE entries (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id   text NOT NULL REFERENCES clients(id),
    entry_date  date NOT NULL,
    memo        text NOT NULL,
    source      text NOT NULL,
    created_by  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    reverses    bigint UNIQUE,              -- an entry can be reversed once
    command_id  text,
    prev_hash   text NOT NULL,
    hash        text NOT NULL,
    UNIQUE (id, client_id),
    FOREIGN KEY (reverses, client_id) REFERENCES entries(id, client_id)
);

CREATE TABLE postings (
    entry_id      bigint NOT NULL,
    client_id     text NOT NULL,
    line          int NOT NULL,
    account_code  text NOT NULL,
    amount        numeric(20, 2) NOT NULL CHECK (amount <> 0),   -- debit positive, credit negative
    currency      char(3) NOT NULL,
    tax_treatment text,
    PRIMARY KEY (entry_id, line),
    FOREIGN KEY (entry_id, client_id) REFERENCES entries(id, client_id),
    FOREIGN KEY (client_id, account_code) REFERENCES accounts(client_id, code)
);

CREATE TABLE commands (
    client_id     text NOT NULL REFERENCES clients(id),
    command_id    text NOT NULL,
    kind          text NOT NULL,
    payload_hash  text NOT NULL,
    result        jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, command_id)
);

CREATE TABLE outbox (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id     text NOT NULL REFERENCES clients(id),
    event_type    text NOT NULL,
    aggregate     text NOT NULL,
    aggregate_id  text NOT NULL,
    payload       jsonb NOT NULL,
    recorded_at   timestamptz NOT NULL DEFAULT now()
);

-- Delivery state lives apart from the immutable event (at-least-once delivery; consumers deduplicate by outbox id).
CREATE TABLE outbox_delivery (
    outbox_id     bigint PRIMARY KEY REFERENCES outbox(id),
    delivered_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit (
    seq        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at         timestamptz NOT NULL,
    actor      text NOT NULL,
    role       text NOT NULL,
    client_id  text,
    action     text NOT NULL,
    payload    jsonb NOT NULL,
    prev_hash  text NOT NULL,
    hash       text NOT NULL
);

CREATE INDEX entries_client ON entries (client_id, id);
CREATE INDEX postings_client_account ON postings (client_id, account_code);

-- ------------------------------------------------------------------------------------------------- immutability
CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP USING ERRCODE = 'AL006';
END $$;

CREATE TRIGGER entries_immutable  BEFORE UPDATE OR DELETE ON entries  FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER postings_immutable BEFORE UPDATE OR DELETE ON postings FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER commands_immutable BEFORE UPDATE OR DELETE ON commands FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER outbox_immutable   BEFORE UPDATE OR DELETE ON outbox   FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER audit_immutable    BEFORE UPDATE OR DELETE ON audit    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER entries_no_truncate  BEFORE TRUNCATE ON entries  FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER postings_no_truncate BEFORE TRUNCATE ON postings FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER commands_no_truncate BEFORE TRUNCATE ON commands FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER outbox_no_truncate   BEFORE TRUNCATE ON outbox   FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER audit_no_truncate    BEFORE TRUNCATE ON audit    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- ------------------------------------------------------------------------------------------------- balance, at commit
CREATE FUNCTION check_entry_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_row   jsonb := to_jsonb(NEW);
    v_id    bigint := coalesce(v_row ->> 'entry_id', v_row ->> 'id')::bigint;   -- fired from postings or entries
    v_lines int;
    v_sum   numeric;
    v_ccy   int;
BEGIN
    SELECT count(*), coalesce(sum(amount), 0), count(DISTINCT currency) INTO v_lines, v_sum, v_ccy
    FROM postings WHERE entry_id = v_id;
    IF v_lines < 2 THEN
        RAISE EXCEPTION 'journal % has % line(s); at least two are required', v_id, v_lines USING ERRCODE = 'AL001';
    END IF;
    IF v_ccy <> 1 THEN
        RAISE EXCEPTION 'journal % mixes currencies', v_id USING ERRCODE = 'AL001';
    END IF;
    IF v_sum <> 0 THEN
        RAISE EXCEPTION 'journal % does not balance (off by %)', v_id, v_sum USING ERRCODE = 'AL001';
    END IF;
    RETURN NULL;
END $$;

-- Deferred: checked when the transaction commits, whichever route inserted the rows (function, owner, migration).
CREATE CONSTRAINT TRIGGER postings_balance AFTER INSERT ON postings DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_entry_balanced();
CREATE CONSTRAINT TRIGGER entries_have_lines AFTER INSERT ON entries DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_entry_balanced();

-- ------------------------------------------------------------------------------------------------- closed periods
CREATE FUNCTION check_open_period() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_closed date;
BEGIN
    -- FOR SHARE serializes with a concurrent close, which updates the client row.
    SELECT closed_through INTO v_closed FROM clients WHERE id = NEW.client_id FOR SHARE;
    IF v_closed IS NOT NULL AND NEW.entry_date <= v_closed THEN
        RAISE EXCEPTION 'books for % are closed through %; an entry dated % needs an authorized reopen first',
            NEW.client_id, v_closed, NEW.entry_date USING ERRCODE = 'AL002';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER entries_open_period BEFORE INSERT ON entries FOR EACH ROW EXECUTE FUNCTION check_open_period();

-- ------------------------------------------------------------------------------------------------- helpers
CREATE FUNCTION sha256_hex(t text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(public.digest(convert_to(t, 'UTF8'), 'sha256'), 'hex')
$$;

-- The clients this session may touch, set per transaction by the API from the user's engagement grants.
CREATE FUNCTION session_clients() RETURNS text[] LANGUAGE sql STABLE AS $$
    SELECT string_to_array(coalesce(nullif(current_setting('agentledger.clients', true), ''), '-'), ',')
$$;

CREATE FUNCTION in_scope(p_client text) RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT '*' = ANY (session_clients()) OR p_client = ANY (session_clients())
$$;

CREATE FUNCTION assert_scope(p_client text) RETURNS void LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF NOT in_scope(p_client) THEN
        RAISE EXCEPTION 'client % is outside this session''s scope', p_client USING ERRCODE = 'AL004';
    END IF;
END $$;

CREATE FUNCTION assert_reviewer(p_role text, p_what text) RETURNS void LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_role IS DISTINCT FROM 'cpa' THEN
        RAISE EXCEPTION '% needs a credentialed reviewer (CPA), not %', p_what, p_role USING ERRCODE = 'AL007';
    END IF;
END $$;

CREATE FUNCTION write_audit(p_actor text, p_role text, p_client text, p_action text, p_payload jsonb) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
    v_prev text;
    v_at   timestamptz := clock_timestamp();
    v_seq  bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(current_schema() || '.audit'));   -- one chain, appended serially
    SELECT hash INTO v_prev FROM audit ORDER BY seq DESC LIMIT 1;
    v_prev := coalesce(v_prev, 'genesis');
    INSERT INTO audit (at, actor, role, client_id, action, payload, prev_hash, hash)
    VALUES (v_at, p_actor, p_role, p_client, p_action, p_payload, v_prev,
            sha256_hex(v_prev || jsonb_build_object('at', v_at, 'actor', p_actor, 'role', p_role, 'client', p_client,
                                                    'action', p_action, 'payload', p_payload)::text))
    RETURNING seq INTO v_seq;
    RETURN v_seq;
END $$;

-- The canonical text an entry's hash covers. Lines are normalized ("100.00", explicit null tax treatment) so the
-- verifier can rebuild exactly the same text from the stored postings.
CREATE FUNCTION entry_canonical(p_client text, p_date date, p_memo text, p_source text, p_actor text, p_reverses bigint,
                                p_lines jsonb) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_build_object('client', p_client, 'date', p_date, 'memo', p_memo, 'source', p_source, 'actor', p_actor,
                              'reverses', p_reverses, 'lines', p_lines)::text
$$;

CREATE FUNCTION entry_lines(p_entry bigint) RETURNS jsonb LANGUAGE sql STABLE AS $$
    SELECT jsonb_agg(jsonb_build_object('account', account_code, 'amount', amount::text, 'tax_treatment', tax_treatment)
                     ORDER BY line)
    FROM postings WHERE entry_id = p_entry
$$;

-- ------------------------------------------------------------------------------------------------- commands
CREATE FUNCTION post_journal(p_client text, p_date date, p_memo text, p_source text, p_actor text, p_role text,
                             p_lines jsonb, p_command_id text DEFAULT NULL, p_reverses bigint DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE
    v_norm    jsonb;
    v_hash    text;
    v_prev    text;
    v_ccy     char(3);
    v_id      bigint;
    v_found   commands%ROWTYPE;
    v_payload text;
    v_sum     numeric;
    v_n       int;
    v_bad     text;
BEGIN
    PERFORM assert_scope(p_client);
    IF jsonb_typeof(p_lines) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'lines must be an array' USING ERRCODE = 'AL005';
    END IF;
    IF EXISTS (SELECT FROM jsonb_array_elements(p_lines) l
               WHERE (l ->> 'amount') IS NULL OR (l ->> 'amount')::numeric <> round((l ->> 'amount')::numeric, 2)) THEN
        RAISE EXCEPTION 'every line needs an amount in whole cents' USING ERRCODE = 'AL005';
    END IF;
    SELECT jsonb_agg(jsonb_build_object('account', l ->> 'account',
                                        'amount', ((l ->> 'amount')::numeric(20, 2))::text,
                                        'tax_treatment', l ->> 'tax_treatment') ORDER BY ord),
           count(*), coalesce(sum((l ->> 'amount')::numeric), 0)
    INTO v_norm, v_n, v_sum
    FROM jsonb_array_elements(p_lines) WITH ORDINALITY AS t(l, ord);
    v_payload := sha256_hex(jsonb_build_object('kind', 'journal.post', 'client', p_client, 'date', p_date, 'memo', p_memo,
                                               'source', p_source, 'lines', v_norm, 'reverses', p_reverses)::text);

    -- One posting at a time per client: keeps the hash chain, the period check and command dedupe serial.
    SELECT functional_currency INTO v_ccy FROM clients WHERE id = p_client FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown client %', p_client USING ERRCODE = 'AL005';
    END IF;
    IF p_command_id IS NOT NULL THEN       -- checked under the lock, so concurrent retries see each other's receipt
        SELECT * INTO v_found FROM commands WHERE client_id = p_client AND command_id = p_command_id;
        IF FOUND THEN
            IF v_found.payload_hash <> v_payload THEN
                RAISE EXCEPTION 'command % was already used for a different journal', p_command_id USING ERRCODE = 'AL003';
            END IF;
            RETURN (v_found.result ->> 'entry_id')::bigint;     -- a retry: the original effect; nothing posts again
        END IF;
    END IF;
    IF v_n < 2 OR v_sum <> 0 THEN                         -- early, clear message; the deferred trigger is the backstop
        RAISE EXCEPTION 'journal needs at least two lines that balance (% line(s), off by %)', v_n, v_sum USING ERRCODE = 'AL001';
    END IF;
    SELECT string_agg(DISTINCT l ->> 'account', ', ') INTO v_bad FROM jsonb_array_elements(v_norm) l
    WHERE NOT EXISTS (SELECT FROM accounts a WHERE a.client_id = p_client AND a.code = l ->> 'account');
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'unknown account(s) for %: %', p_client, v_bad USING ERRCODE = 'AL005';
    END IF;

    SELECT hash INTO v_prev FROM entries WHERE client_id = p_client ORDER BY id DESC LIMIT 1;
    v_prev := coalesce(v_prev, 'genesis');
    v_hash := sha256_hex(v_prev || entry_canonical(p_client, p_date, p_memo, p_source, p_actor, p_reverses, v_norm));
    INSERT INTO entries (client_id, entry_date, memo, source, created_by, reverses, command_id, prev_hash, hash)
    VALUES (p_client, p_date, p_memo, p_source, p_actor, p_reverses, p_command_id, v_prev, v_hash)
    RETURNING id INTO v_id;
    INSERT INTO postings (entry_id, client_id, line, account_code, amount, currency, tax_treatment)
    SELECT v_id, p_client, (ord - 1)::int, l ->> 'account', (l ->> 'amount')::numeric(20, 2), v_ccy, l ->> 'tax_treatment'
    FROM jsonb_array_elements(v_norm) WITH ORDINALITY AS t(l, ord);
    INSERT INTO outbox (client_id, event_type, aggregate, aggregate_id, payload)
    VALUES (p_client, 'journal.posted', 'entry', v_id::text,
            jsonb_build_object('entry_id', v_id, 'date', p_date, 'source', p_source, 'reverses', p_reverses));
    PERFORM write_audit(p_actor, p_role, p_client, 'ledger.posted',
                        jsonb_build_object('entry_id', v_id, 'memo', p_memo, 'source', p_source, 'command_id', p_command_id));
    IF p_command_id IS NOT NULL THEN
        INSERT INTO commands (client_id, command_id, kind, payload_hash, result)
        VALUES (p_client, p_command_id, 'journal.post', v_payload, jsonb_build_object('entry_id', v_id));
    END IF;
    RETURN v_id;
END $$;

CREATE FUNCTION reverse_journal(p_client text, p_entry bigint, p_date date, p_reason text, p_actor text, p_role text,
                                p_command_id text DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE
    v_lines jsonb;
BEGIN
    PERFORM assert_scope(p_client);
    IF coalesce(btrim(p_reason), '') = '' THEN
        RAISE EXCEPTION 'a reversal needs a reason' USING ERRCODE = 'AL005';
    END IF;
    SELECT jsonb_agg(jsonb_build_object('account', account_code, 'amount', (-amount)::text, 'tax_treatment', tax_treatment)
                     ORDER BY line)
    INTO v_lines FROM postings WHERE entry_id = p_entry AND client_id = p_client;
    IF v_lines IS NULL THEN
        RAISE EXCEPTION 'no entry % for %', p_entry, p_client USING ERRCODE = 'AL005';
    END IF;
    IF EXISTS (SELECT FROM entries WHERE reverses = p_entry) AND p_command_id IS NULL THEN
        RAISE EXCEPTION 'entry % is already reversed', p_entry USING ERRCODE = 'AL005';
    END IF;
    RETURN post_journal(p_client, p_date, 'Reversal of #' || p_entry || ': ' || p_reason, 'reversal', p_actor, p_role,
                        v_lines, p_command_id, p_entry);
END $$;

CREATE FUNCTION close_period(p_client text, p_through date, p_actor text, p_role text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE
    v_prev date;
BEGIN
    PERFORM assert_scope(p_client);
    PERFORM assert_reviewer(p_role, 'closing the books');
    SELECT closed_through INTO v_prev FROM clients WHERE id = p_client FOR UPDATE;
    IF v_prev IS NOT NULL AND p_through < v_prev THEN
        RAISE EXCEPTION 'closing to an earlier date is a reopen' USING ERRCODE = 'AL005';
    END IF;
    UPDATE clients SET closed_through = p_through WHERE id = p_client;
    PERFORM write_audit(p_actor, p_role, p_client, 'period.closed', jsonb_build_object('through', p_through, 'previous', v_prev));
    INSERT INTO outbox (client_id, event_type, aggregate, aggregate_id, payload)
    VALUES (p_client, 'period.closed', 'client', p_client, jsonb_build_object('through', p_through));
END $$;

CREATE FUNCTION reopen_period(p_client text, p_back_to date, p_actor text, p_role text, p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE
    v_prev date;
BEGIN
    PERFORM assert_scope(p_client);
    PERFORM assert_reviewer(p_role, 'reopening the books');
    IF coalesce(btrim(p_reason), '') = '' THEN
        RAISE EXCEPTION 'a reopen needs a reason' USING ERRCODE = 'AL005';
    END IF;
    SELECT closed_through INTO v_prev FROM clients WHERE id = p_client FOR UPDATE;
    UPDATE clients SET closed_through = p_back_to WHERE id = p_client;
    PERFORM write_audit(p_actor, p_role, p_client, 'period.reopened',
                        jsonb_build_object('from', v_prev, 'to', p_back_to, 'reason', p_reason));
    INSERT INTO outbox (client_id, event_type, aggregate, aggregate_id, payload)
    VALUES (p_client, 'period.reopened', 'client', p_client, jsonb_build_object('to', p_back_to, 'reason', p_reason));
END $$;

CREATE FUNCTION add_client(p_id text, p_name text, p_kind text, p_actor text, p_role text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
BEGIN
    PERFORM assert_scope(p_id);
    INSERT INTO clients (id, name, kind) VALUES (p_id, p_name, p_kind);
    PERFORM write_audit(p_actor, p_role, p_id, 'client.created', jsonb_build_object('name', p_name, 'kind', p_kind));
END $$;

CREATE FUNCTION add_account(p_client text, p_code text, p_name text, p_type text, p_actor text, p_role text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
BEGIN
    PERFORM assert_scope(p_client);
    INSERT INTO accounts (client_id, code, name, type) VALUES (p_client, p_code, p_name, p_type);
    PERFORM write_audit(p_actor, p_role, p_client, 'account.created', jsonb_build_object('code', p_code, 'type', p_type));
END $$;

CREATE FUNCTION mark_delivered(p_outbox_id bigint) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path FROM CURRENT AS $$
    INSERT INTO outbox_delivery (outbox_id) VALUES (p_outbox_id) ON CONFLICT DO NOTHING RETURNING true
$$;

CREATE FUNCTION verify_chain(p_client text) RETURNS TABLE (ok boolean, checked bigint, broken_at bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE
    v_prev text := 'genesis';
    v_n    bigint := 0;
    r      record;
BEGIN
    PERFORM assert_scope(p_client);
    FOR r IN SELECT * FROM entries WHERE client_id = p_client ORDER BY id LOOP
        IF r.prev_hash <> v_prev OR r.hash <> sha256_hex(v_prev || entry_canonical(r.client_id, r.entry_date, r.memo, r.source,
                                                                                    r.created_by, r.reverses, entry_lines(r.id))) THEN
            ok := false; checked := v_n; broken_at := r.id; RETURN NEXT; RETURN;
        END IF;
        v_prev := r.hash;
        v_n := v_n + 1;
    END LOOP;
    ok := true; checked := v_n; broken_at := NULL; RETURN NEXT;
END $$;

-- ------------------------------------------------------------------------------------------------- row-level security
ALTER TABLE clients  ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE entries  ENABLE ROW LEVEL SECURITY;
ALTER TABLE postings ENABLE ROW LEVEL SECURITY;
ALTER TABLE commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox   ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit    ENABLE ROW LEVEL SECURITY;
CREATE POLICY scope_clients  ON clients  FOR SELECT USING (in_scope(id));
CREATE POLICY scope_accounts ON accounts FOR SELECT USING (in_scope(client_id));
CREATE POLICY scope_entries  ON entries  FOR SELECT USING (in_scope(client_id));
CREATE POLICY scope_postings ON postings FOR SELECT USING (in_scope(client_id));
CREATE POLICY scope_commands ON commands FOR SELECT USING (in_scope(client_id));
CREATE POLICY scope_outbox   ON outbox   FOR SELECT USING (in_scope(client_id));
CREATE POLICY scope_audit    ON audit    FOR SELECT USING (client_id IS NOT NULL AND in_scope(client_id));

-- ------------------------------------------------------------------------------------------------- privileges
DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO agentledger_app', current_schema());
    EXECUTE format('REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA %I FROM PUBLIC', current_schema());
END $$;
GRANT SELECT ON clients, accounts, entries, postings, commands, outbox, outbox_delivery, audit TO agentledger_app;
GRANT EXECUTE ON FUNCTION post_journal, reverse_journal, close_period, reopen_period, add_client, add_account,
    mark_delivered, verify_chain, session_clients, in_scope, sha256_hex TO agentledger_app;
