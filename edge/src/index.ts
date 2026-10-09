// The edge: one Worker in front of the API container (wrangler.jsonc, docs/DEPLOY.md). It picks a container
// instance for the client, forwards the request with the headers the API relies on, and logs one line per request.
// It also hosts the orchestration the API cannot (ADR-0003): the filing Workflow (workflows/submission.ts) and the
// outbox relay (relay.ts), both of which reach the API only through the Durable Object with the workflow credential.
// Nothing else lives here: sessions, authorization and every rule stay in the API.
import { Container, getContainer } from "@cloudflare/containers";

import { WORKFLOW_MARK, instanceFor } from "./api";
import { relay } from "./relay";
import { FilingSubmission, type FilingParams } from "./workflows/submission";

export { FilingSubmission };

export interface Env {
	API: DurableObjectNamespace<ApiContainer>;
	FILING: Workflow<FilingParams>;
	API_INSTANCES: string;
	ENVIRONMENT: string;
	AGENTLEDGER_WORKFLOW_TOKEN?: string;
	[name: string]: unknown;
}

// Everything the container reads from its environment: settings (vars) and secrets alike.
const FORWARDED = /^(AGENTLEDGER_|WORKOS_|ANTHROPIC_)/;

export class ApiContainer extends Container<Env> {
	override defaultPort = 8080;

	constructor(ctx: Container<Env>["ctx"], env: Env) {
		super(ctx, env);
		// Staging sleeps after half an hour idle; production stays warm through a working day's quiet spells.
		this.sleepAfter = env.ENVIRONMENT === "production" ? "4h" : "30m";
		const vars: Record<string, string> = {};
		for (const [name, value] of Object.entries(env)) {
			if (FORWARDED.test(name) && typeof value === "string") vars[name] = value;
		}
		this.envVars = vars;
	}

	override async fetch(request: Request): Promise<Response> {
		// A cold start pulls the image and boots the API; wait for the port instead of failing the first request.
		await this.startAndWaitForPorts({ cancellationOptions: { portReadyTimeoutMS: 120_000 } });
		return this.containerFetch(request);
	}
}

// The workflow runtime's door into the API (api/internal.py): reached only from inside this Worker, through the
// Durable Object, by the Workflow and the relay. From the internet it does not exist.
const INTERNAL = /^\/internal(\/|$)/;
// Set by the API on a response whose request emitted outbox events; the relay runs at once instead of waiting for
// the cron. Never shown to the client.
const OUTBOX_MARK = "X-AgentLedger-Outbox";

export default {
	async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
		const started = Date.now();
		const requestId = crypto.randomUUID();
		const url = new URL(request.url);
		if (INTERNAL.test(url.pathname)) {
			console.log(JSON.stringify({ method: request.method, path: url.pathname, status: 404, ms: 0, requestId, internal: true }));
			return new Response("Not Found", { status: 404 });
		}
		const ip = request.headers.get("cf-connecting-ip") ?? "";
		const instance = instanceFor(env, ip);

		// Set, never appended: a client cannot smuggle an address or a scheme past the edge. uvicorn applies these
		// (--proxy-headers), so the API sees the real client IP (sign-in lockout) and https (secure cookies). The
		// workflow's own marker never arrives from outside either.
		const headers = new Headers(request.headers);
		headers.delete(WORKFLOW_MARK);
		headers.set("X-Request-Id", requestId);
		headers.set("X-Forwarded-For", ip);
		headers.set("X-Forwarded-Proto", "https");
		headers.set("X-Forwarded-Host", url.host);
		const upstream = await getContainer(env.API, instance).fetch(new Request(request, { headers }));

		const response = new Response(upstream.body, upstream);
		if (response.headers.get(OUTBOX_MARK) === "1") {
			response.headers.delete(OUTBOX_MARK);
			ctx.waitUntil(relay(env));
		}
		response.headers.set("X-Request-Id", requestId);
		response.headers.set("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
		console.log(JSON.stringify({ method: request.method, path: url.pathname, status: response.status,
			ms: Date.now() - started, requestId, instance }));
		return response;
	},

	// The backstop: every minute, whatever the outbox still holds (a relay that failed, a container that restarted
	// between the commit and its response) is delivered.
	async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
		ctx.waitUntil(relay(env));
	},
} satisfies ExportedHandler<Env>;
