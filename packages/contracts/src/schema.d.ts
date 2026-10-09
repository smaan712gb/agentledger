/**
 * This file was generated from openapi.json by `npm run generate` (openapi-typescript). Do not edit it.
 * openapi.json is written by `python scripts/export_openapi.py` from the API's request and response models
 * (src/agentledger/api/schemas.py); src/types.ts names the schemas the app uses (docs/WEB.md).
 */
export interface paths {
    "/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Index */
        get: operations["index"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Agents */
        get: operations["agents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agents/{agent_id}/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Run Agent */
        post: operations["run_agent"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/ask": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Ask */
        post: operations["ask"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/audit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Audit Trail */
        get: operations["audit_trail"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/accept": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Auth Accept */
        post: operations["auth_accept"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/config": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Auth Config */
        get: operations["auth_config"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Auth Events */
        get: operations["auth_events"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/idp/callback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Idp Callback
         * @description The provider redirects the browser here. The session token goes to the app in the URL fragment, which is
         *     never sent to a server.
         */
        get: operations["idp_callback"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/idp/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Idp Start */
        get: operations["idp_start"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/invite": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Auth Invite */
        post: operations["auth_invite"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Auth Login */
        post: operations["auth_login"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Auth Logout */
        post: operations["auth_logout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/mfa": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Auth Mfa */
        post: operations["auth_mfa"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/step-up": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Auth Step Up */
        post: operations["auth_step_up"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/users": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Auth Users */
        get: operations["auth_users"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/users/{user_id}/disable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Auth Disable */
        post: operations["auth_disable"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/users/{user_id}/grants": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Auth Grants */
        get: operations["auth_grants"];
        put?: never;
        /** Auth Grant */
        post: operations["auth_grant"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/users/{user_id}/grants/{client_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Auth Revoke */
        delete: operations["auth_revoke"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/auth/users/{user_id}/reviewer": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Auth Reviewer */
        post: operations["auth_reviewer"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/automations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Automations List */
        get: operations["automations_list"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/automations/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Automations Run */
        post: operations["automations_run"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/calculators": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Calculators */
        get: operations["calculators"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/calculators/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Calculate */
        post: operations["calculate"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Clients */
        get: operations["clients"];
        put?: never;
        /** Create Client */
        post: operations["create_client"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Client Detail */
        get: operations["client_detail"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/bank/post": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Bank Post */
        post: operations["bank_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/bank/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Bank Preview */
        post: operations["bank_preview"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/deals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Biz Add Deal */
        post: operations["biz_add_deal"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Client Documents
         * @description A client's documents, newest first and paged (deleted ones excluded): the same fields the client detail embeds
         *     for its 100 most recent. Follow `next_cursor` until it is null.
         */
        get: operations["client_documents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/entries": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Client Entries */
        get: operations["client_entries"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/entries/{entry_id}/reverse": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reverse Entry */
        post: operations["reverse_entry"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/export/{plugin_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Export */
        get: operations["export"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/facts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * Update Facts
         * @description Merge the facts sent into the client's profile; a fact sent as null is removed. Returns the whole profile.
         */
        patch: operations["update_facts"];
        trace?: never;
    };
    "/api/clients/{client_id}/holds": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Client Holds */
        get: operations["client_holds"];
        put?: never;
        /** Place Hold */
        post: operations["place_hold"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/integrity/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Integrity Run */
        post: operations["integrity_run"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/invoices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Biz Invoice
         * @description Send an Idempotency-Key header to make retries safe: the same key returns the original invoice.
         */
        post: operations["biz_invoice"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/invoices/{invoice_id}/pay": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Biz Pay */
        post: operations["biz_pay"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/opportunities": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Opportunities */
        get: operations["opportunities"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/parties": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Biz Parties */
        get: operations["biz_parties"];
        put?: never;
        /** Biz Add Party */
        post: operations["biz_add_party"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/periods/{action}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Period Action
         * @description Close the books through a date, or reopen them (reviewers only; every change is in the audit trail).
         */
        post: operations["period_action"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/returns": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Client Returns */
        get: operations["client_returns"];
        put?: never;
        /** Create Return */
        post: operations["create_return"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/tax-year-events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Record Tax Year Event
         * @description Record a filing, amendment, payment or 'no return required' that retention counts from (returns filed outside
         *     AgentLedger), with its evidence.
         */
        post: operations["record_tax_year_event"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/templates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Client Templates */
        get: operations["client_templates"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/templates/{template_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Post Template */
        post: operations["post_template"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/clients/{client_id}/vendors/{party_id}/pay": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Biz Pay Vendor */
        post: operations["biz_pay_vendor"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/coverage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Coverage
         * @description What the product actually supports, per form, year and jurisdiction (spec §7). Read by every signed-in user and
         *     by the deployment smoke check.
         */
        get: operations["get_coverage"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/crm/engagements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Crm New Engagement */
        post: operations["crm_new_engagement"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/crm/engagements/{eid}/stage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Crm Stage */
        post: operations["crm_stage"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/crm/pipeline": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Crm Pipeline */
        get: operations["crm_pipeline"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/dashboard": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Dashboard */
        get: operations["dashboard"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/deals/{deal_id}/stage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Biz Deal Stage */
        post: operations["biz_deal_stage"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/design/{what}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Design */
        post: operations["design"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/documents/{doc_id}/assign": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Assign Doc
         * @description File a review-queue document to a client, or move a filed one (a reviewer, with a reason on record).
         */
        post: operations["assign_doc"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/documents/{doc_id}/basis-release": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Release Document Basis
         * @description A CPA records that the property a basis record supports was disposed of: the record is then kept as a tax record
         *     of the disposition year.
         */
        post: operations["release_document_basis"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/documents/{doc_id}/file": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Doc File
         * @description The document's bytes, as a download (`Content-Disposition: attachment`, application/octet-stream). With
         *     `inline=1`, a PDF, PNG or JPEG is served inline with its real media type inside a sandbox
         *     (`Content-Security-Policy: sandbox; default-src 'none'`, `X-Content-Type-Options: nosniff`) so the app can show it;
         *     every other type stays a download. Either way a signed link (`dl`) stands in for the session header.
         */
        get: operations["doc_file"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/documents/{doc_id}/retention": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Document Retention
         * @description When a document may be deleted, computed now (or why it may not be yet), its hold and the years it supports.
         */
        get: operations["document_retention"];
        put?: never;
        /**
         * Confirm Document Retention
         * @description A CPA confirms a document's tax year (or that it has none) and retention class; until then it is never deleted.
         */
        post: operations["confirm_document_retention"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/documents/{doc_id}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Doc Versions */
        get: operations["doc_versions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/documents/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Review Queue */
        get: operations["review_queue"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/documents/upload": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Upload */
        post: operations["upload"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/evidence/due": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Evidence Due
         * @description What a retention run would delete today (held documents excluded).
         */
        get: operations["evidence_due"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/evidence/integrity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Evidence Integrity
         * @description Live documents whose bytes are missing, deletions not finished, (verify=true) every stored object read back and
         *     authenticated against its recorded hash, and `anchors` (F-13): the audit chain against every anchor in the object
         *     store, the anchors' signatures, the lock probe's last outcome and whether an anchor is overdue.
         */
        get: operations["evidence_integrity"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/evidence/purge": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Evidence Purge
         * @description Delete evidence whose retention has ended. Never automatic; each deletion leaves a receipt.
         */
        post: operations["evidence_purge"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/findings/{finding_id}/resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resolve Finding */
        post: operations["resolve_finding"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/holds/{hold_id}/release": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Release Hold */
        post: operations["release_hold"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/hooks/{firm_id}/{client_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Inbound Hook */
        post: operations["inbound_hook"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/links": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Make Link */
        post: operations["make_link"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Me */
        get: operations["get_me"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Messages */
        get: operations["messages"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/messages/{mid}/send": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Send */
        post: operations["send"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Models */
        get: operations["models"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/oss": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Oss */
        get: operations["oss"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/packs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Packs
         * @description The industry packs a client can be onboarded on (domains/*.yaml), shared by every firm.
         */
        get: operations["packs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/platform/firms": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Platform Firms */
        get: operations["platform_firms"];
        put?: never;
        /** Platform Create Firm */
        post: operations["platform_create_firm"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/platform/firms/{firm_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Platform Firm */
        get: operations["platform_firm"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/platform/sso-mfa": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Platform Sso Mfa
         * @description Record (or revoke) that an organization's own identity provider enforces MFA, so its SSO sign-ins count.
         */
        post: operations["platform_sso_mfa"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/playbooks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Playbooks */
        get: operations["playbooks"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/plugins": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Plugins */
        get: operations["plugins"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/plugins/{plugin_id}/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Plugin Run */
        post: operations["plugin_run"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/plugins/request": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Request Connector
         * @description Ask the AI Engineer to build a catalog connector (becomes a work item; result needs approval).
         */
        post: operations["request_connector"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/precedents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** New Precedent */
        post: operations["new_precedent"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/proposals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Proposals */
        get: operations["proposals"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/proposals/{pid}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Proposal */
        get: operations["proposal"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/proposals/{pid}/{decision}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide */
        post: operations["decide"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Return */
        get: operations["get_return"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/{action}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Return Action */
        post: operations["return_action"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/amend": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Return Amend */
        post: operations["return_amend"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/compute": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Compute Return */
        post: operations["compute_return"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/confirm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Confirm Return Amounts */
        post: operations["confirm_return_amounts"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/conflicts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Return Conflicts
         * @description Where a document disagrees with what the return holds (nothing was overwritten).
         */
        get: operations["return_conflicts"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/conflicts/{conflict_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resolve Return Conflict */
        post: operations["resolve_return_conflict"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/documents/{doc_id}/disposition": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Return Document Disposition
         * @description Account for a filed document the return does not use: entered by hand, or not applicable, with a reason.
         */
        post: operations["return_document_disposition"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/facts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Return Fact History
         * @description Every value a field has had, where each came from, and what it superseded.
         */
        get: operations["return_fact_history"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/inputs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Put Return Inputs */
        put: operations["put_return_inputs"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/populate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Populate Return */
        post: operations["populate_return"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/recalculation-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Return Recalc Preview
         * @description What the return would be under today's rules; never stored on a filed return.
         */
        get: operations["return_recalc_preview"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/{rid}/void": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Void Return
         * @description A CPA voids a return that will not be filed through AgentLedger (abandoned, or filed with other software).
         */
        post: operations["void_return"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/returns/individual": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Compute Individual Return
         * @description Compute a Form 1040 from facts and source documents. Nothing is stored (so the deployment smoke check may
         *     call it too).
         */
        post: operations["compute_individual_return"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/rules": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Rules */
        get: operations["rules"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/rules/{rule_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Rule */
        get: operations["rule"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/staleness": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Staleness */
        get: operations["staleness"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tasks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Tasks */
        get: operations["list_tasks"];
        put?: never;
        /** New Task */
        post: operations["new_task"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tasks/{task_id}/done": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Task Done */
        post: operations["task_done"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/users": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Users
         * @description Demo identity picker. Exists only in dev mode; production sign-in is /api/auth/login.
         */
        get: operations["list_users"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/healthz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Healthz
         * @description Liveness for the edge and the release smoke test (scripts/smoke.py waits for `build` to reach the deployed
         *     commit). No firm or platform store is touched.
         */
        get: operations["healthz"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/legacy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Legacy
         * @description The previous (no-build) interface, reachable at /legacy#/... while the React app (apps/web) owns / at the edge.
         *     The same page as /: its asset URLs are absolute (/static/...), so it works from either path.
         */
        get: operations["legacy"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AcceptRequest */
        AcceptRequest: {
            /** Name */
            name: string;
            /** Password */
            password: string;
            /** Token */
            token: string;
        };
        /**
         * AssignedDocument
         * @description The document row after filing.
         */
        AssignedDocument: {
            /** Client Id */
            client_id?: string | null;
            /** Id */
            id: string;
            /** Status */
            status: string;
        } & {
            [key: string]: unknown;
        };
        /** AssignRequest */
        AssignRequest: {
            /** Client Id */
            client_id: string;
            /**
             * Move Reason
             * @description required (10+ characters) to move a document already filed elsewhere
             */
            move_reason?: string | null;
        };
        /** AuthConfig */
        AuthConfig: {
            /**
             * Identity
             * @description AGENTLEDGER_IDENTITY: 'local' (password + one-time code) or 'workos'
             */
            identity: string;
            /**
             * Password Sign In
             * @description true, or 'platform administrators only' when firms sign in through the provider
             */
            password_sign_in: boolean | string;
        };
        /** AuthEvent */
        AuthEvent: {
            /** At */
            at: string;
            /** Detail */
            detail?: string | null;
            /** Email */
            email?: string | null;
            /** Event */
            event: string;
            /** Firm Id */
            firm_id?: string | null;
            /** Id */
            id: number;
            /** Ip */
            ip?: string | null;
            /** User Id */
            user_id?: string | null;
        } & {
            [key: string]: unknown;
        };
        /** Balance */
        Balance: {
            /** Balance */
            balance: string;
            /** Code */
            code: string;
            /** Name */
            name: string;
            /** Type */
            type: string;
        } & {
            [key: string]: unknown;
        };
        /** Body_upload */
        Body_upload: {
            /** Client Id */
            client_id?: string | null;
            /** File */
            file: string;
        };
        /** ChainStatus */
        ChainStatus: {
            /** Broken At */
            broken_at?: number | string | null;
            /** Checked */
            checked: number;
            /** Head */
            head?: string | null;
            /** Ok */
            ok: boolean;
        } & {
            [key: string]: unknown;
        };
        /**
         * Client
         * @description A row of the clients table (ledger/store.py), JSON columns parsed.
         */
        Client: {
            /** Aliases */
            aliases: string[];
            /**
             * Closed Through
             * @description ISO date; postings on or before it are frozen
             */
            closed_through: string | null;
            /** Consent 7216 At */
            consent_7216_at: string | null;
            /** Created At */
            created_at: string;
            /** Domain */
            domain: string;
            /** Emails */
            emails: string[];
            /** Entity Type */
            entity_type: string | null;
            facts: components["schemas"]["ClientFacts"];
            /** Formed Under */
            formed_under: string;
            /** Id */
            id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "business" | "individual";
            /** Name */
            name: string;
            /** Tax Id Last4 */
            tax_id_last4: string | null;
        } & {
            [key: string]: unknown;
        };
        /** ClientDetail */
        ClientDetail: {
            /**
             * Ar
             * @description business clients only
             */
            ar?: {
                [key: string]: unknown;
            } | null;
            /** Balances */
            balances: components["schemas"]["Balance"][];
            chain: components["schemas"]["ChainStatus"];
            client: components["schemas"]["Client"];
            /** Deadlines */
            deadlines: components["schemas"]["Deadline"][];
            /**
             * Deals
             * @description business clients only
             */
            deals?: {
                [key: string]: unknown;
            } | null;
            /**
             * Documents
             * @description the 100 most recent; the full list is paged at /documents
             */
            documents: components["schemas"]["ClientDocument"][];
            /** Findings */
            findings: components["schemas"]["Finding"][];
            /** Integrity */
            integrity: number;
            /** Kpis */
            kpis: components["schemas"]["Kpi"][];
            /**
             * M1
             * @description business clients only
             */
            m1?: {
                [key: string]: unknown;
            } | null;
            /** Opportunities */
            opportunities: components["schemas"]["Opportunity"][];
            pack?: components["schemas"]["PackSummary"] | null;
            /** Tasks */
            tasks: components["schemas"]["Task"][];
            /**
             * Vendors 1099
             * @description business clients only
             */
            vendors_1099?: {
                [key: string]: unknown;
            }[] | null;
            /** Year */
            year: number;
        } & {
            [key: string]: unknown;
        };
        /**
         * ClientDocument
         * @description A document as the client detail and GET /api/clients/{id}/documents list them.
         */
        ClientDocument: {
            /** Channel */
            channel: string;
            /** Classified By */
            classified_by?: string | null;
            /** Confidence */
            confidence?: number | null;
            /** Doc Type */
            doc_type?: string | null;
            /** Id */
            id: string;
            /** Original Name */
            original_name: string;
            /** Received At */
            received_at: string;
            /**
             * Status
             * @description filed, or needs_review
             */
            status: string;
            /** Summary */
            summary?: string | null;
            /** Tax Year */
            tax_year?: number | null;
            /** Vault Path */
            vault_path?: string | null;
        } & {
            [key: string]: unknown;
        };
        /**
         * ClientFacts
         * @description Profile facts are free-form; the keys the app reads by name are declared. PATCH /api/clients/{id}/facts merges
         *     the keys sent; a key sent as null is removed.
         */
        ClientFacts: {
            /** Accounting Basis */
            accounting_basis?: string | null;
            /** Employees */
            employees?: number | string | null;
            /** State */
            state?: string | null;
        } & {
            [key: string]: unknown;
        };
        /** CreateClientRequest */
        CreateClientRequest: {
            /**
             * Aliases
             * @default []
             */
            aliases?: string[];
            /** Consent 7216 At */
            consent_7216_at?: string | null;
            /**
             * Domain
             * @default general
             */
            domain?: string;
            /**
             * Emails
             * @default []
             */
            emails?: string[];
            /** Entity Type */
            entity_type?: string | null;
            facts?: components["schemas"]["ClientFacts"];
            /**
             * Formed Under
             * @default domestic
             */
            formed_under?: string;
            /**
             * Id
             * @description lowercase letters, digits, '-' or '_'; up to 64 characters
             */
            id: string;
            /**
             * Kind
             * @default business
             * @enum {string}
             */
            kind?: "business" | "individual";
            /** Name */
            name: string;
            /** Tax Id Last4 */
            tax_id_last4?: string | null;
        };
        /** CreateClientResult */
        CreateClientResult: {
            /** Accounts Created */
            accounts_created: number;
            /** Id */
            id: string;
        };
        /** CreateFirmRequest */
        CreateFirmRequest: {
            /** Admin Email */
            admin_email: string;
            /** Id */
            id: string;
            /** Name */
            name: string;
        };
        /** CreateFirmResult */
        CreateFirmResult: {
            /** Admin Invite Token */
            admin_invite_token: string;
            firm: components["schemas"]["Firm"];
        };
        /**
         * Dashboard
         * @description GET /api/dashboard: the cards every role gets, the firm's work for CPAs, the client's tasks for a client.
         */
        Dashboard: {
            /** Adopted */
            adopted?: {
                [key: string]: unknown;
            }[] | null;
            /** Ai */
            ai?: {
                [key: string]: unknown;
            } | null;
            /** Ai Usage */
            ai_usage?: {
                [key: string]: unknown;
            }[] | null;
            /** Clients */
            clients: components["schemas"]["DashboardCard"][];
            /** Kb */
            kb?: {
                [key: string]: unknown;
            } | null;
            /** Pending */
            pending?: {
                [key: string]: unknown;
            }[] | null;
            /** Review Queue */
            review_queue?: number | null;
            /** Runs */
            runs?: {
                [key: string]: unknown;
            }[] | null;
            /** Staleness */
            staleness?: {
                [key: string]: unknown;
            }[] | null;
            /** Tasks */
            tasks?: components["schemas"]["Task"][] | null;
            /** Today */
            today: string;
        } & {
            [key: string]: unknown;
        };
        /** DashboardCard */
        DashboardCard: {
            /** Docs This Month */
            docs_this_month: number;
            /** Domain */
            domain: string;
            /** Id */
            id: string;
            /** Integrity */
            integrity: number;
            /** Kind */
            kind: string;
            /** Name */
            name: string;
            /** Open Findings */
            open_findings: number;
            /** Open Tasks */
            open_tasks: number;
        };
        /** Deadline */
        Deadline: {
            /** Citation */
            citation?: string | null;
            /** Client Id */
            client_id?: string | null;
            /** Days */
            days: number;
            /** Due */
            due: string;
            /** Form */
            form?: string | null;
            /** Id */
            id?: string | null;
            /** Title */
            title: string;
        } & {
            [key: string]: unknown;
        };
        /** DisableRequest */
        DisableRequest: {
            /**
             * Disabled
             * @default true
             */
            disabled?: boolean;
        };
        /**
         * DocumentPage
         * @description One page of a client's documents, newest first; `next_cursor` is null on the last page.
         */
        DocumentPage: {
            /** Items */
            items: components["schemas"]["ClientDocument"][];
            /** Next Cursor */
            next_cursor?: string | null;
            /**
             * Total
             * @description the client's documents not deleted under retention, all pages
             */
            total: number;
        };
        /**
         * DocumentVersion
         * @description A stored version of a document (the storage locator is withheld).
         */
        DocumentVersion: {
            /** Created At */
            created_at: string;
            /** Created By */
            created_by: string;
            /** Document Id */
            document_id: string;
            /** Reason */
            reason: string;
            /** Sha256 */
            sha256: string;
            /** Size */
            size: number;
            /** Version */
            version: number;
        } & {
            [key: string]: unknown;
        };
        /**
         * DuplicateDocument
         * @description The same bytes were stored before: nothing new was written.
         */
        DuplicateDocument: {
            /** Client Id */
            client_id?: string | null;
            /**
             * Duplicate
             * @constant
             */
            duplicate: true;
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Status */
            status: string;
            /** Vault Path */
            vault_path?: string | null;
        };
        /** Engagement */
        Engagement: {
            /** Client Id */
            client_id: string;
            /** Client Name */
            client_name: string;
            /** Created At */
            created_at?: string | null;
            /** Due Date */
            due_date?: string | null;
            /** Fee */
            fee?: string | null;
            /** Id */
            id: number;
            /** Open Tasks */
            open_tasks: number;
            /** Owner */
            owner?: string | null;
            /** Stage */
            stage: string;
            /** Tax Year */
            tax_year?: number | null;
            /** Type */
            type: string;
        } & {
            [key: string]: unknown;
        };
        /**
         * EnrolStep
         * @description The account's first sign-in (or an accepted invitation): enrol the authenticator with this secret.
         */
        EnrolStep: {
            /** Challenge */
            challenge: string;
            /**
             * Next
             * @constant
             */
            next: "enroll";
            /** Otpauth Uri */
            otpauth_uri: string;
            /** Secret */
            secret: string;
        };
        /** Finding */
        Finding: {
            /** Check Id */
            check_id: string;
            /** Citation */
            citation?: string | null;
            /** Client Id */
            client_id: string;
            /** Detail */
            detail: string;
            /** Evidence */
            evidence?: unknown;
            /** First Seen */
            first_seen: string;
            /** Id */
            id: string;
            /**
             * Owner
             * @enum {string}
             */
            owner: "client" | "cpa" | "both";
            /**
             * Resolutions
             * @default []
             */
            resolutions?: components["schemas"]["FindingResolution"][];
            /**
             * Severity
             * @enum {string}
             */
            severity: "info" | "low" | "medium" | "high" | "critical";
            /** Status */
            status: string;
            /** Tax Year */
            tax_year?: number | null;
            /** Title */
            title: string;
        } & {
            [key: string]: unknown;
        };
        /** FindingResolution */
        FindingResolution: {
            /** Action */
            action: string;
            /** Actor */
            actor: string;
            /** At */
            at: string;
            /** Finding Id */
            finding_id?: string | null;
            /** Id */
            id?: number | null;
            /** Note */
            note: string;
            /** Role */
            role: string;
        } & {
            [key: string]: unknown;
        };
        /** Firm */
        Firm: {
            /** Created At */
            created_at: string;
            /** Deleted At */
            deleted_at?: string | null;
            /** Id */
            id: string;
            /** Name */
            name: string;
            /**
             * Status
             * @description active, provisioning, or deleting
             */
            status: string;
        } & {
            [key: string]: unknown;
        };
        /**
         * FirmRef
         * @description The firm a session belongs to, as GET /api/me names it.
         */
        FirmRef: {
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Status */
            status: string;
        };
        /**
         * FirmUser
         * @description A firm member as GET /api/auth/users lists them (platform.py `public_user`).
         */
        FirmUser: {
            /** Client Id */
            client_id: string | null;
            /** Disabled */
            disabled: boolean;
            /** Email */
            email: string;
            /**
             * Engaged
             * @description staff only: the clients they are engaged on
             */
            engaged?: string[] | null;
            /** Firm Id */
            firm_id: string;
            /** Id */
            id: string;
            /** Last Login At */
            last_login_at: string | null;
            /** Mfa Enrolled At */
            mfa_enrolled_at: string | null;
            /** Name */
            name: string;
            /** Reviewer */
            reviewer: boolean;
            /**
             * Role
             * @enum {string}
             */
            role: "platform_admin" | "firm_admin" | "cpa" | "staff" | "client";
        } & {
            [key: string]: unknown;
        };
        /** Health */
        Health: {
            /** Backend */
            backend: string;
            /**
             * Build
             * @description AGENTLEDGER_BUILD or the BUILD_SHA file; 'dev' in a checkout
             */
            build: string;
            /** Ok */
            ok: boolean;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** IdpStart */
        IdpStart: {
            /**
             * Url
             * @description the identity provider's hosted sign-in page; the agentledger_idp cookie binds the browser
             */
            url: string;
        };
        /**
         * IngestedDocument
         * @description One part of an upload that was stored (intake/pipeline.py `ingest`; a zip or an email yields several).
         */
        IngestedDocument: {
            /** Client Id */
            client_id?: string | null;
            /** Confidence */
            confidence: number;
            /** Doc Type */
            doc_type?: string | null;
            /** Id */
            id: string;
            /**
             * Match
             * @description how the client was matched
             */
            match: string;
            /** Name */
            name: string;
            /** Retention Class */
            retention_class?: string | null;
            /**
             * Status
             * @description filed, or needs_review (waiting in the inbox)
             */
            status: string;
            /** Suggested Client */
            suggested_client?: string | null;
            /** Tax Year */
            tax_year?: number | null;
            /** Vault Path */
            vault_path?: string | null;
        };
        /** InviteRequest */
        InviteRequest: {
            /**
             * Client Id
             * @description required for a client user
             */
            client_id?: string | null;
            /** Email */
            email: string;
            /**
             * Firm Id
             * @description platform administrators may name the firm; members invite into their own
             */
            firm_id?: string | null;
            /**
             * Role
             * @enum {string}
             */
            role: "firm_admin" | "cpa" | "staff" | "client";
        };
        /** InviteResult */
        InviteResult: {
            /** Expires In Days */
            expires_in_days: number;
            /** Invite Token */
            invite_token: string;
        };
        /** Kpi */
        Kpi: {
            /** Id */
            id?: string | null;
            /**
             * Missing
             * @description the fact or balance the KPI needs and does not have
             */
            missing?: string | null;
            /** Title */
            title: string;
            /**
             * Unit
             * @default
             */
            unit?: string;
            /** Value */
            value?: string | null;
        } & {
            [key: string]: unknown;
        };
        /** LinkRequest */
        LinkRequest: {
            /**
             * Path
             * @description /api/documents/{id}/file or /api/clients/{id}/export/{plugin}
             */
            path: string;
        };
        /** LinkResult */
        LinkResult: {
            /** Expires In */
            expires_in: number;
            /**
             * Url
             * @description the path with a signed `dl` token; a session header is not needed
             */
            url: string;
        };
        /** LoginRequest */
        LoginRequest: {
            /** Email */
            email: string;
            /** Password */
            password: string;
        };
        /**
         * Me
         * @description GET /api/me: the public account, the session, the two role views and the firm.
         */
        Me: {
            /** Auth Method */
            auth_method?: string | null;
            /**
             * Base Role
             * @enum {string}
             */
            base_role: "platform_admin" | "firm_admin" | "cpa" | "staff" | "client";
            /** Client Id */
            client_id?: string | null;
            /**
             * Disabled
             * @default false
             */
            disabled?: boolean;
            /** Email */
            email?: string | null;
            /**
             * Engaged
             * @description staff only: the clients they are engaged on
             */
            engaged?: string[] | null;
            /** @description null for platform administrators */
            firm?: components["schemas"]["FirmRef"] | null;
            /** Firm Id */
            firm_id?: string | null;
            /** Fresh At */
            fresh_at?: string | null;
            /** Id */
            id: string;
            /** Last Login At */
            last_login_at?: string | null;
            /** Mfa Enrolled At */
            mfa_enrolled_at?: string | null;
            /** Name */
            name: string;
            /**
             * Reviewer
             * @default false
             */
            reviewer?: boolean;
            /**
             * Role
             * @enum {string}
             */
            role: "cpa" | "client" | "platform_admin";
            /** Session Id */
            session_id?: string | null;
        } & {
            [key: string]: unknown;
        };
        /** MfaRequest */
        MfaRequest: {
            /** Challenge */
            challenge: string;
            /** Code */
            code: string;
        };
        /** MfaResult */
        MfaResult: {
            /** Token */
            token: string;
            user?: components["schemas"]["SessionUser"] | null;
        };
        /**
         * MfaStep
         * @description A second factor is always required: the account is enrolled, enter the current code.
         */
        MfaStep: {
            /** Challenge */
            challenge: string;
            /**
             * Next
             * @constant
             */
            next: "mfa";
        };
        /** Ok */
        Ok: {
            /**
             * Ok
             * @default true
             */
            ok?: boolean;
        };
        /** Opportunity */
        Opportunity: {
            /** Aggressiveness */
            aggressiveness?: string | null;
            /** Category */
            category: string;
            /** Freshness */
            freshness?: unknown;
            /** Missing Fact */
            missing_fact?: string | null;
            /** Playbook Id */
            playbook_id: string;
            /**
             * Status
             * @description applies, or needs_facts
             */
            status: string;
            /** Summary */
            summary: string;
            /** Title */
            title: string;
        } & {
            [key: string]: unknown;
        };
        /**
         * Pack
         * @description An industry pack (domains/*.yaml) a client can be onboarded on.
         */
        Pack: {
            /** Description */
            description: string;
            /** Facts */
            facts: string[];
            /** Id */
            id: string;
            /** Status */
            status: string;
            /** Title */
            title: string;
        };
        /** PackSummary */
        PackSummary: {
            /** Description */
            description: string;
            /** Facts */
            facts: string[];
            /** Id */
            id: string;
            /** Title */
            title: string;
        };
        /**
         * ReviewDocument
         * @description A document waiting in the review queue (GET /api/documents/review).
         */
        ReviewDocument: {
            /** Channel */
            channel: string;
            /** Classified By */
            classified_by?: string | null;
            /** Confidence */
            confidence?: number | null;
            /** Doc Type */
            doc_type?: string | null;
            /** Id */
            id: string;
            /** Original Name */
            original_name: string;
            /** Received At */
            received_at: string;
            /** Sender */
            sender?: string | null;
            /** Summary */
            summary?: string | null;
            /** Tax Year */
            tax_year?: number | null;
        } & {
            [key: string]: unknown;
        };
        /**
         * SessionUser
         * @description The person a fresh session belongs to (platform.py `session_user`).
         */
        SessionUser: {
            /** Auth Method */
            auth_method?: string | null;
            /** Client Id */
            client_id: string | null;
            /** Disabled */
            disabled: boolean;
            /** Email */
            email: string;
            /**
             * Engaged
             * @description staff only: the clients they are engaged on
             */
            engaged?: string[] | null;
            /** Firm Id */
            firm_id: string;
            /** Fresh At */
            fresh_at?: string | null;
            /** Id */
            id: string;
            /** Last Login At */
            last_login_at: string | null;
            /** Mfa Enrolled At */
            mfa_enrolled_at: string | null;
            /** Name */
            name: string;
            /** Reviewer */
            reviewer: boolean;
            /**
             * Role
             * @enum {string}
             */
            role: "platform_admin" | "firm_admin" | "cpa" | "staff" | "client";
            /** Session Id */
            session_id: string;
        } & {
            [key: string]: unknown;
        };
        /** StepUpRequest */
        StepUpRequest: {
            /** Code */
            code: string;
        };
        /** Task */
        Task: {
            /** Assignee */
            assignee: string;
            /** Client Id */
            client_id?: string | null;
            /** Client Name */
            client_name?: string | null;
            /** Created At */
            created_at?: string | null;
            /**
             * Detail
             * @default
             */
            detail?: string;
            /** Done At */
            done_at?: string | null;
            /** Done Note */
            done_note?: string | null;
            /** Due */
            due?: string | null;
            /** Engagement Id */
            engagement_id?: number | null;
            /** Id */
            id: number;
            /** Source */
            source: string;
            /** Status */
            status: string;
            /** Title */
            title: string;
        } & {
            [key: string]: unknown;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, unknown>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    index: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/html": string;
                };
            };
        };
    };
    agents: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    run_agent: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                agent_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ask: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    audit_trail: {
        parameters: {
            query?: {
                client_id?: string | null;
            };
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_accept: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AcceptRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EnrolStep"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_config: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AuthConfig"];
                };
            };
        };
    };
    auth_events: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AuthEvent"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    idp_callback: {
        parameters: {
            query?: {
                code?: string;
                state?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    idp_start: {
        parameters: {
            query?: {
                invite?: string;
                purpose?: string;
            };
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IdpStart"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_invite: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InviteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InviteResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_login: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MfaStep"] | components["schemas"]["EnrolStep"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_logout: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Ok"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_mfa: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MfaRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MfaResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_step_up: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StepUpRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Ok"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_users: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FirmUser"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_disable: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["DisableRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Ok"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_grants: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": string[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_grant: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_revoke: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
                user_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    auth_reviewer: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    automations_list: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    automations_run: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    calculators: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    calculate: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                name: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    clients: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Client"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_client: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateClientRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreateClientResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    client_detail: {
        parameters: {
            query?: {
                year?: number | null;
            };
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClientDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    bank_post: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                }[];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    bank_preview: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    biz_add_deal: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    client_documents: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentPage"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    client_entries: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reverse_entry: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
                entry_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    export: {
        parameters: {
            query?: {
                dl?: string;
            };
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
                plugin_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_facts: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClientFacts"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClientFacts"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    client_holds: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    place_hold: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    integrity_run: {
        parameters: {
            query?: {
                year?: number | null;
            };
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    biz_invoice: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
                "idempotency-key"?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    biz_pay: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
                "idempotency-key"?: string;
            };
            path: {
                client_id: string;
                invoice_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    opportunities: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    biz_parties: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    biz_add_party: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    period_action: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                action: string;
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    client_returns: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_return: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    record_tax_year_event: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    client_templates: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_template: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                client_id: string;
                template_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    biz_pay_vendor: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
                "idempotency-key"?: string;
            };
            path: {
                client_id: string;
                party_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_coverage: {
        parameters: {
            query?: {
                year?: number | null;
            };
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    crm_new_engagement: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    crm_stage: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                eid: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    crm_pipeline: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: components["schemas"]["Engagement"][];
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    dashboard: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Dashboard"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    biz_deal_stage: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                deal_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    design: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                what: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    assign_doc: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                doc_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AssignRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AssignedDocument"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    release_document_basis: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                doc_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    doc_file: {
        parameters: {
            query?: {
                dl?: string;
                inline?: boolean;
            };
            header?: {
                authorization?: string;
            };
            path: {
                doc_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    document_retention: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                doc_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    confirm_document_retention: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                doc_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    doc_versions: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                doc_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentVersion"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    review_queue: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewDocument"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    upload: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_upload"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": (components["schemas"]["IngestedDocument"] | components["schemas"]["DuplicateDocument"])[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    evidence_due: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    evidence_integrity: {
        parameters: {
            query?: {
                verify?: boolean;
            };
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    evidence_purge: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resolve_finding: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                finding_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    release_hold: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                hold_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    inbound_hook: {
        parameters: {
            query?: never;
            header?: {
                "x-agentledger-signature"?: string;
            };
            path: {
                client_id: string;
                firm_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    make_link: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LinkRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LinkResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_me: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Me"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    messages: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    send: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                mid: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    models: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    oss: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    packs: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Pack"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    platform_firms: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Firm"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    platform_create_firm: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateFirmRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreateFirmResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    platform_firm: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                firm_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Firm"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    platform_sso_mfa: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    playbooks: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    plugins: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    plugin_run: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                plugin_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_connector: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    new_precedent: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    proposals: {
        parameters: {
            query?: {
                status?: string | null;
            };
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    proposal: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                pid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decide: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                decision: string;
                pid: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_return: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    return_action: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                action: string;
                rid: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    return_amend: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    compute_return: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    confirm_return_amounts: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    return_conflicts: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resolve_return_conflict: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                conflict_id: number;
                rid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    return_document_disposition: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                doc_id: string;
                rid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    return_fact_history: {
        parameters: {
            query: {
                path: string;
            };
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_return_inputs: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    populate_return: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    return_recalc_preview: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    void_return: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    compute_individual_return: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    rules: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    rule: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    staleness: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_tasks: {
        parameters: {
            query?: {
                client_id?: string | null;
            };
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Task"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    new_task: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    task_done: {
        parameters: {
            query?: never;
            header?: {
                authorization?: string;
            };
            path: {
                task_id: number;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": {
                    [key: string]: unknown;
                };
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_users: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
        };
    };
    healthz: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Health"];
                };
            };
        };
    };
    legacy: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/html": string;
                };
            };
        };
    };
}
