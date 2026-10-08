"""The AI Engineer runs AI-written code only inside a container (audit finding 8 residual, backlog F-03).

The live test starts a real container (python:3.12-slim) with the Engineer's flags and probes it from inside:
no secrets, not root, no network, read-only root filesystem, and nothing of the host but the worktree.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from agentledger.foundry.agents import builders

CFG = {"container": {"image": "agentledger-engineer:local", "env": {"OLLAMA_API_BASE": "http://host.docker.internal:11434"}}}


def test_container_flags_isolate_the_step(tmp_path):
    argv = builders.container_argv(["python", "-m", "pytest", "-q"], tmp_path, CFG, network="none", pass_env=[])
    assert argv[:3] == ["docker", "run", "--rm"]
    for flag in (["--network", "none"], ["--cap-drop", "ALL"], ["--security-opt", "no-new-privileges"]):
        i = argv.index(flag[0])
        assert argv[i + 1] == flag[1]
    assert "--read-only" in argv and "--pids-limit" in argv and "--memory" in argv
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert mounts == [f"{tmp_path}:/work"]                            # only the throwaway worktree
    assert "OLLAMA_API_BASE=http://host.docker.internal:11434" not in argv   # no model route when offline
    assert argv[-5:] == ["agentledger-engineer:local", "python", "-m", "pytest", "-q"]


def test_agent_step_gets_named_keys_never_values(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    argv = builders.container_argv(["claude", "-p", "x"], tmp_path, CFG, network="bridge", pass_env=["ANTHROPIC_API_KEY"])
    assert "ANTHROPIC_API_KEY" in argv and not any("sk-ant-secret-value" in a for a in argv)
    assert "OLLAMA_API_BASE=http://host.docker.internal:11434" in argv


def test_engineer_refuses_without_a_container_runtime(foundry, monkeypatch):
    root = foundry.paths.root
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base"], cwd=root, check=True)
    work = foundry.paths.state / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "w1.json").write_text(json.dumps({"status": "open", "title": "t", "items": ["x"]}), encoding="utf-8")
    real_which = shutil.which
    monkeypatch.setattr(builders.shutil, "which", lambda name: None if name == "docker" else real_which(name))
    ran = []
    monkeypatch.setattr(builders.subprocess, "run", lambda *a, **k: ran.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    res = builders.AgentResult()
    spec = builders.AgentSpec(id="engineer", kind="engineer")
    builders.engineer(foundry, spec, res)
    assert any(a["type"] == "engineer_unavailable" and "container" in a["note"] for a in res.alerts)
    assert not any("aider" in str(x) or "claude" in str(x) for x in ran)   # no coding agent ran on the host


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0


PROBE = r'''
import json, os, socket
out = {"uid": os.getuid(), "env": sorted(os.environ)}
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3); out["network"] = True
except OSError:
    out["network"] = False
try:
    open("/etc/agentledger-probe", "w"); out["root_writable"] = True
except OSError:
    out["root_writable"] = False
open("/work/written.txt", "w").write("ok")
out["work"] = sorted(os.listdir("/work"))
print(json.dumps(out))
'''


@pytest.mark.skipif(not _docker_ready(), reason="needs a running Docker engine")
def test_live_container_has_no_secrets_network_or_host_access(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", "master-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://owner:pw@host/db")
    (tmp_path / "probe.py").write_text(PROBE, encoding="utf-8")
    cfg = {"container": {"image": "python:3.12-slim", "user": "1000:1000"}}
    argv = builders.container_argv(["python", "/work/probe.py"], tmp_path, cfg, network="none", pass_env=[])
    r = subprocess.run(argv, capture_output=True, text=True, timeout=300, env=builders.sandbox_env())
    assert r.returncode == 0, r.stderr[-500:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["uid"] != 0
    assert out["network"] is False
    assert out["root_writable"] is False
    assert "written.txt" in out["work"] and (tmp_path / "written.txt").exists()   # the worktree is the only output
    assert not {"AGENTLEDGER_MASTER_KEY", "DATABASE_URL", "NEON_API_KEY", "ANTHROPIC_API_KEY"} & set(out["env"])
