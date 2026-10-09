// One Workflow instance per attempt at one submission (ADR-0003, backlog F-08): the Cloudflare mirror of
// src/agentledger/workflow/flows/submission.py. Step names and timings come from the same steps.json; every step is
// one system command on the API container (api/internal.py) under an idempotency key, so a retried step converges and
// the domain, not this class, holds the business state. The Workflow never approves, releases, reconciles or resends:
// those are a person's commands, which the endpoint refuses to it (403) and which never appear here.
//
// A 409 (the domain refused, or the command conflicted), a 403 and a 404 end the step for good (NonRetryableError);
// 5xx and network failures are retried on the plan's schedule. Payloads carry ids only.
import { WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep, type WorkflowStepConfig } from "cloudflare:workers";
import { NonRetryableError } from "cloudflare:workflows";

import plan from "../../../src/agentledger/workflow/flows/steps.json";
import { ApiRefused, command } from "../api";
import type { Env } from "../index";

export type FilingParams = { firm_id: string; return_id: string; submission_id: string };

// What a command returns (returns/filing.py `_outcome`): ids, hashes, statuses, codes. Structured-cloneable, as a
// step result must be.
type Scalar = string | number | boolean | null;
type Outcome = { status: string; [key: string]: Scalar | Scalar[] };

const FINAL = new Set(["accepted", "rejected", "cancelled", "superseded"]);

function seconds(s: number): `${number} seconds` {
	return `${s} seconds`;
}

function expand(schedule: { every_s: number; times: number }[]): number[] {
	const out: number[] = [];
	for (const part of schedule) for (let i = 0; i < part.times; i++) out.push(part.every_s);
	return out;
}

const RETRIES: WorkflowStepConfig = {
	retries: { limit: plan.retries.command.limit, delay: seconds(plan.retries.command.delay_s), backoff: plan.retries.command.backoff as "exponential" },
	timeout: seconds(plan.retries.command.timeout_s),
};

export class FilingSubmission extends WorkflowEntrypoint<Env, FilingParams> {
	override async run(event: Readonly<WorkflowEvent<FilingParams>>, step: WorkflowStep): Promise<Record<string, unknown>> {
		const { firm_id: firm, submission_id: sid } = event.payload;
		const instance = event.instanceId;

		// Keyed by the instance (one per attempt at a submission): a later attempt is a new command, never a replay.
		const run = (name: string, suffix: string, extra: Record<string, unknown> = {}) =>
			step.do(suffix, RETRIES, async () => {
				try {
					const out = await command(this.env, firm, instance, name, `${instance}:${suffix}`, { submission_id: sid, ...extra });
					return out as unknown as Outcome;
				} catch (e) {
					if (e instanceof ApiRefused) throw new NonRetryableError(e.message);
					throw e;
				}
			});

		// 1. Transmit once. Retries exhausted (the transmitter never answered): the submission is unknown.
		let out: Outcome;
		try {
			out = await run("transmit_submission", "transmit");
		} catch (e) {
			if (e instanceof NonRetryableError) throw e;
			out = await run("mark_unknown", "mark-unknown", { note: "no answer from the transmitter after retries" });
		}
		let status = String(out.status);

		// 2. Unknown: ask the transmitter by the planned id, with backoff, until the deadline; then a person.
		if (status === "unknown") {
			let n = 0;
			for (const delay of plan.lookup.intervals_s) {
				n++;
				await step.sleep(`lookup-wait-${n}`, seconds(delay));
				out = await run("lookup_submission", `lookup-${n}`);
				status = String(out.status);
				if (status !== "unknown") break;
			}
			if (status === "unknown") await run("notify_operator", "notify-unknown", { reason: "transmission outcome unknown" });
			n = 0;
			while (status === "unknown") {
				n++;
				try {
					await step.waitForEvent(`wait-reconciled-${n}`, { type: "submission-reconciled", timeout: seconds(plan.reconcile_wait_s) });
				} catch {
					continue; // timed out: re-arm, a person has not decided yet
				}
				out = await run("lookup_submission", `after-reconcile-${n}`);
				status = String(out.status);
			}
			if (status === "queued") return { outcome: "reconciled_not_submitted", submission_id: sid };
		}

		// 3. Transmitted: poll for the acknowledgement on the schedule, then tell a person and keep polling.
		if (status === "transmitted") {
			const schedule = expand(plan.poll.schedule);
			const after = plan.poll.after_notice;
			const all = schedule.concat(new Array<number>(after.times).fill(after.every_s));
			for (let n = 1; n <= all.length; n++) {
				if (n === schedule.length + 1) await run("notify_operator", "notify-unacknowledged", { reason: "no acknowledgement within the deadline" });
				await step.sleep(`poll-wait-${n}`, seconds(all[n - 1] ?? after.every_s));
				out = await run("poll_acks", `poll-ack-${n}`);
				status = String(out.status);
				if (FINAL.has(status)) break;
			}
		}
		if (status === "rejected") await run("notify_operator", "notify-rejected", { reason: "rejected" });
		return { outcome: status, submission_id: sid };
	}
}
