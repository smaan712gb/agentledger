// The Worker's own way into the API container: through the `API` Durable Object, never the public hostname, with the
// workflow runtime's credential (api/internal.py). Shared by the relay and the Workflow classes.
import { getContainer } from "@cloudflare/containers";

import type { Env } from "./index";

export const WORKFLOW_MARK = "X-AgentLedger-Workflow";

// FNV-1a (32 bit): one key always lands on the same container instance (the public fetch uses the client address;
// the Workflow and the relay use the firm id).
export function hash(text: string): number {
	let h = 0x811c9dc5;
	for (let i = 0; i < text.length; i++) {
		h ^= text.charCodeAt(i);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	return h >>> 0;
}

export function instanceFor(env: Env, key: string): string {
	const instances = Math.max(1, Math.floor(Number(env.API_INSTANCES)) || 1);
	return `api-${hash(key) % instances}`;
}

/** The container refused for good: the Workflow maps this to NonRetryableError, the relay logs and moves on. */
export class ApiRefused extends Error {
	constructor(public readonly status: number, public readonly code: string, detail: string) {
		super(`${code}: ${detail}`);
		this.name = "ApiRefused";
	}
}

type Refusal = { detail?: { code?: string; detail?: string } | string };

/** One request to /internal/* on the container that serves `firm`. 4xx is final (ApiRefused); 5xx and network
 * failures throw a plain Error, which a Workflow step retries. */
export async function internal(env: Env, firm: string, path: string, init: { method?: string; body?: unknown } = {}): Promise<unknown> {
	const token = env.AGENTLEDGER_WORKFLOW_TOKEN;
	if (typeof token !== "string" || token.length < 32) throw new ApiRefused(403, "no_credential", "AGENTLEDGER_WORKFLOW_TOKEN is not set");
	const headers: Record<string, string> = { Authorization: `Bearer ${token}`, [WORKFLOW_MARK]: "1", Accept: "application/json" };
	if (init.body !== undefined) headers["Content-Type"] = "application/json";
	const request = new Request(`https://api.internal${path}`, {
		method: init.method ?? (init.body === undefined ? "GET" : "POST"),
		headers,
		body: init.body === undefined ? undefined : JSON.stringify(init.body),
	});
	const response = await getContainer(env.API, instanceFor(env, firm)).fetch(request);
	if (response.ok) return response.json();
	if (response.status >= 500) throw new Error(`internal ${path}: ${response.status}`);
	let code = `http_${response.status}`;
	let detail = response.statusText;
	try {
		const body = (await response.json()) as Refusal;
		if (typeof body.detail === "string") detail = body.detail;
		else if (body.detail) {
			code = body.detail.code ?? code;
			detail = body.detail.detail ?? detail;
		}
	} catch {
		// a non-JSON refusal keeps the status text
	}
	throw new ApiRefused(response.status, code, detail);
}

export type CommandResult = { result: Record<string, unknown>; replayed: boolean; at: string };

/** The command envelope (workflow/bus.py): the same JSON the in-process bus dispatches. */
export async function command(env: Env, firm: string, instanceId: string, name: string, idempotencyKey: string,
	payload: Record<string, unknown>): Promise<Record<string, unknown>> {
	const out = (await internal(env, firm, "/internal/commands", {
		body: { firm_id: firm, name, idempotency_key: idempotencyKey, payload, instance_id: instanceId },
	})) as CommandResult;
	return out.result;
}
