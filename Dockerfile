# The API container (wrangler.jsonc: containers[].image), built by `wrangler deploy` from the repository root.
# One image serves every environment: settings and secrets arrive from the Worker (edge/src/index.ts) at start,
# never baked in. Nothing authoritative lives on container disk; PostgreSQL and R2 hold all state (ADR-0001).
#
#   docker build -t agentledger-api:local .
#   docker run --rm -e AGENTLEDGER_DEV_AUTH=1 -e AGENTLEDGER_AGENTS=0 -p 18080:8080 agentledger-api:local
#   curl http://localhost:18080/healthz
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AGENTLEDGER_HOME=/app PYTHONPATH=/app/src

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Dependency layer: the runtime dependencies from pyproject.toml, nothing from [dev]. The PolicyEngine oracle
# (policyengine-us, hundreds of MB) is opt-in with --build-arg WITH_ORACLE=1; without it the cross-check reports the
# oracle as unavailable (returns/store.py handles the ImportError) and the deterministic engine is unaffected.
ARG WITH_ORACLE=0
COPY pyproject.toml /tmp/build/pyproject.toml
RUN python - <<'EOF'
import os, subprocess, tomllib
meta = tomllib.load(open("/tmp/build/pyproject.toml", "rb"))["project"]
deps = list(meta["dependencies"])
if os.environ.get("WITH_ORACLE") == "1":
    deps += meta["optional-dependencies"]["oracle"]
subprocess.check_call(["uv", "pip", "install", "--system", "--no-cache", *deps])
EOF

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app
WORKDIR /app

# The repository layout is kept under /app: coverage.py finds coverage/ from the package location (parents[2]),
# app.py serves src/agentledger/web from the package, and the Foundry reads rules/, config/, domains/, playbooks/,
# golden/ and evals/ under AGENTLEDGER_HOME. Each directory is copied on its own, so nothing else can slip in.
COPY --chown=app:app src/ /app/src/
COPY --chown=app:app config/ /app/config/
COPY --chown=app:app rules/ /app/rules/
COPY --chown=app:app domains/ /app/domains/
COPY --chown=app:app playbooks/ /app/playbooks/
COPY --chown=app:app golden/ /app/golden/
COPY --chown=app:app coverage/ /app/coverage/
COPY --chown=app:app evals/ /app/evals/
# `dev` in the repository; the release workflow writes the commit SHA here before `wrangler deploy`, and
# GET /healthz reports it so the smoke test can tell when the rollout has reached the new image.
COPY --chown=app:app BUILD_SHA /app/BUILD_SHA

# Ephemeral scratch for the process (platform cache, proposals, tenant directories); never a store of record.
RUN mkdir -p /app/state /app/tenants && chown app:app /app/state /app/tenants

USER 1000:1000
EXPOSE 8080
# Only the Durable Object can reach this port. The Worker sets X-Forwarded-For/-Proto/-Host, which uvicorn applies
# here: the app marks the IdP cookie `secure` only on https and uses the client IP for sign-in lockout.
CMD ["uvicorn", "agentledger.api.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "30"]
