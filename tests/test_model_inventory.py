"""Model inventory merge, new-model detection and safe retirement (no network)."""

from veritas.ai import inventory as inv
from veritas.ai.inventory import Model, Route


def src(rows):
    return lambda: [("x", m) for m in rows]


def test_merge_routes_across_hosts_and_pick_cheapest(tmp_path):
    p = tmp_path / "inv.json"
    sources = {
        "workers-ai": src([Model(id=inv.norm("@cf/qwen/qwen3.8-27b-fp8"), names=["@cf/qwen/qwen3.8-27b-fp8"],
                                 routes=[Route("workers-ai", "@cf/qwen/qwen3.8-27b-fp8", in_platform=True)])]),
        "hf-router": src([Model(id=inv.norm("Qwen/Qwen3.8-27B"), names=["Qwen/Qwen3.8-27B"],
                                routes=[Route("hf:deepinfra", "Qwen/Qwen3.8-27B:deepinfra", 0.2, 2.5),
                                        Route("hf:novita", "Qwen/Qwen3.8-27B:novita", 0.42, 3.0)])]),
    }
    out = inv.refresh(p, sources)
    m = out["models"]["qwen/qwen3.8-27b"]
    assert {r["host"] for r in m["routes"]} == {"workers-ai", "hf:deepinfra", "hf:novita"}
    assert Model(**{**m, "routes": [Route(**r) for r in m["routes"]]}).cheapest.host == "hf:deepinfra"
    assert out["new"] == ["qwen/qwen3.8-27b"]


def test_outage_does_not_retire_and_real_removal_does(tmp_path):
    p = tmp_path / "inv.json"
    a = Model(id="nvidia/nemotron-3-super", names=["nvidia/nemotron-3-super"], routes=[Route("nvidia", "nvidia/nemotron-3-super")])
    inv.refresh(p, {"nvidia": src([a])})

    def down():
        raise RuntimeError("timeout")

    out = inv.refresh(p, {"nvidia": down})
    assert "nvidia/nemotron-3-super" in out["models"] and out["retired"] == []   # source down: keep
    assert out["sources"]["nvidia"].startswith("unavailable")
    out = inv.refresh(p, {"nvidia": src([])})
    assert out["retired"] == ["nvidia/nemotron-3-super"]                          # source answered without it
    assert out["models"] == {}
