// The outbox relay (ADR-0003, backlog F-08): what the domain wrote to its outbox becomes Workflow instances and
// signals. The same rules as the Python runner's `deliver` (src/agentledger/workflow/runner.py), so the self-host
// profile and Cloudflare behave alike:
//   submission.queued      -> an instance per attempt at a submission (a state's only once the federal is accepted)
//   submission.accepted    -> (US-FED) the linked state instances start
//   submission.reconciled  -> the waiting instance is signalled
// Everything else is information. Each event is marked delivered after it is acted on; the Workflow's own
// idempotency (instance ids, command keys) absorbs a redelivery. Runs from the cron (every minute, the backstop)
// and right after any API response that emitted events (X-AgentLedger-Outbox).
import { ApiRefused, internal } from "./api";
import type { Env } from "./index";
import type { FilingParams } from "./workflows/submission";

const FEDERAL = "US-FED";
const PAGE = 100;

type OutboxEvent = {
	id: number;
	client_id: string;
	event_type: string;
	aggregate: string;
	aggregate_id: string;
	payload: Record<string, unknown>;
};

type Linked = { submission_id: string; attempt?: number; jurisdiction?: string };

export function instanceIdFor(p: Record<string, unknown>): string {
	return `filing-${String(p.submission_id)}-${Number(p.attempt) || 1}`;
}

async function start(env: Env, firm: string, p: Record<string, unknown>): Promise<void> {
	const params: FilingParams = { firm_id: firm, return_id: String(p.return_id ?? ""), submission_id: String(p.submission_id) };
	try {
		await env.FILING.create({ id: instanceIdFor(p), params, retention: { successRetention: "7 days", errorRetention: "7 days" } });
	} catch (e) {
		// Created on an earlier delivery: the event is done.
		if (!/exist/i.test(String((e as Error).message))) throw e;
	}
}

async function signal(env: Env, id: string, type: string, payload: Record<string, unknown>): Promise<void> {
	try {
		const instance = await env.FILING.get(id);
		await instance.sendEvent({ type, payload });
	} catch (e) {
		// No such instance, or not waiting (the local runner handled it, or it completed): nothing to resume.
		console.log(JSON.stringify({ relay: "signal", id, type, skipped: String((e as Error).message) }));
	}
}

export async function deliver(env: Env, firm: string, ev: OutboxEvent): Promise<void> {
	const p = ev.payload ?? {};
	switch (ev.event_type) {
		case "submission.queued":
			if (!p.linked_to || p.linked_accepted) await start(env, firm, p);
			break;
		case "submission.accepted":
			if (p.jurisdiction === FEDERAL) {
				for (const linked of (p.linked as Linked[] | undefined) ?? []) {
					await start(env, firm, { ...p, submission_id: linked.submission_id, attempt: linked.attempt ?? 1 });
				}
			}
			break;
		case "submission.reconciled":
			await signal(env, instanceIdFor(p), "submission-reconciled", p);
			break;
		default:
			break;
	}
}

export async function relayFirm(env: Env, firm: string): Promise<number> {
	let after = 0;
	let delivered = 0;
	for (;;) {
		const page = (await internal(env, firm, `/internal/outbox?firm_id=${encodeURIComponent(firm)}&after=${after}&limit=${PAGE}`)) as {
			events: OutboxEvent[];
			next: number;
		};
		for (const ev of page.events) {
			await deliver(env, firm, ev);
			await internal(env, firm, `/internal/outbox/${ev.id}/delivered`, { body: { firm_id: firm } });
			delivered++;
		}
		if (page.events.length < PAGE) return delivered;
		after = page.next;
	}
}

/** Every active firm's outbox, one firm after another; a firm's failure is logged and does not stop the others. */
export async function relay(env: Env): Promise<void> {
	let firms: string[] = [];
	try {
		firms = ((await internal(env, "platform", "/internal/firms")) as { firms: string[] }).firms;
	} catch (e) {
		console.log(JSON.stringify({ relay: "firms", error: String((e as Error).message) }));
		return;
	}
	for (const firm of firms) {
		try {
			const n = await relayFirm(env, firm);
			if (n) console.log(JSON.stringify({ relay: firm, delivered: n }));
		} catch (e) {
			const refused = e instanceof ApiRefused ? e.code : undefined;
			console.log(JSON.stringify({ relay: firm, error: String((e as Error).message), refused }));
		}
	}
}
