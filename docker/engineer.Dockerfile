# The AI Engineer's sandbox (config/foundry.yaml: engineer.container.image).
# It holds the project's dependencies and the coding agents, never the code or any secret: the Engineer mounts one
# throwaway worktree at /work and runs as an unprivileged user with a read-only root filesystem.
#
#   docker build -t agentledger-engineer:local -f docker/engineer.Dockerfile .
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# The project's dependencies (from pyproject.toml) in the system interpreter; the code under test comes from the
# mounted worktree via PYTHONPATH. The PolicyEngine oracle is left out (large): its cross-checks skip here and run in CI.
COPY pyproject.toml /tmp/build/pyproject.toml
RUN python - <<'EOF'
import subprocess, tomllib
meta = tomllib.load(open("/tmp/build/pyproject.toml", "rb"))["project"]
deps = meta["dependencies"] + meta["optional-dependencies"]["dev"]
subprocess.check_call(["uv", "pip", "install", "--system", "--no-cache", *deps])
EOF

# Coding agents as isolated tools: aider pins its own library versions, so it gets its own environment and can never
# change (or conflict with) the dependencies the tests run against.
ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin
RUN uv tool install --no-cache --python 3.12 aider-chat \
    && npm install -g @anthropic-ai/claude-code && npm cache clean --force

RUN useradd --uid 1000 --create-home engineer
USER 1000:1000
WORKDIR /work
