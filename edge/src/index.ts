// The edge: one Worker in front of the API container (wrangler.jsonc, docs/DEPLOY.md). It picks a container
// instance for the client, forwards the request with the headers the API relies on, and logs one line per request.
// Nothing else lives here: sessions, authorization and every rule stay in the API.
import { Container, getContainer } from "@cloudflare/containers";

interface Env {
	API: DurableObjectNamespace<ApiContainer>;
	API_INSTANCES: string;
	ENVIRONMENT: string;
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

// FNV-1a (32 bit): the same client address always lands on the same instance.
function hash(text: string): number {
	let h = 0x811c9dc5;
	for (let i = 0; i < text.length; i++) {
		h ^= text.charCodeAt(i);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	return h >>> 0;
}

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		const started = Date.now();
		const requestId = crypto.randomUUID();
		const url = new URL(request.url);
		const ip = request.headers.get("cf-connecting-ip") ?? "";
		const instances = Math.max(1, Math.floor(Number(env.API_INSTANCES)) || 1);
		const instance = `api-${hash(ip) % instances}`;

		// Set, never appended: a client cannot smuggle an address or a scheme past the edge. uvicorn applies these
		// (--proxy-headers), so the API sees the real client IP (sign-in lockout) and https (secure cookies).
		const headers = new Headers(request.headers);
		headers.set("X-Request-Id", requestId);
		headers.set("X-Forwarded-For", ip);
		headers.set("X-Forwarded-Proto", "https");
		headers.set("X-Forwarded-Host", url.host);
		const upstream = await getContainer(env.API, instance).fetch(new Request(request, { headers }));

		const response = new Response(upstream.body, upstream);
		response.headers.set("X-Request-Id", requestId);
		response.headers.set("Strict-Transport-Security", "max-age=31536000; includeSubDomains");
		console.log(JSON.stringify({ method: request.method, path: url.pathname, status: response.status,
			ms: Date.now() - started, requestId, instance }));
		return response;
	},
} satisfies ExportedHandler<Env>;
