import os

from agentledger.envfile import load


def test_env_file_loads_without_overriding(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# comment\nOPENSTATES_API_KEY=abc123\nexport DATA_GOV_API_KEY=\"q w\"\nREAL=fromfile\n", encoding="utf-8")
    monkeypatch.delenv("OPENSTATES_API_KEY", raising=False)
    monkeypatch.delenv("DATA_GOV_API_KEY", raising=False)
    monkeypatch.setenv("REAL", "fromenv")
    assert sorted(load(tmp_path)) == ["DATA_GOV_API_KEY", "OPENSTATES_API_KEY"]
    assert os.environ["OPENSTATES_API_KEY"] == "abc123" and os.environ["DATA_GOV_API_KEY"] == "q w"
    assert os.environ["REAL"] == "fromenv"   # real environment always wins
