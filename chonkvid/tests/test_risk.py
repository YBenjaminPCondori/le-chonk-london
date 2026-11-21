import asyncio
import os
import tempfile

os.environ["CHONKVID_MODE"] = "mock"
_TMP = tempfile.mkdtemp(prefix="chonkvid-risk-test-")
os.environ.setdefault("CHONKVID_RESULTS_DIR", os.path.join(_TMP, "results"))
os.environ.setdefault("CHONKVID_RUNS_DIR", os.path.join(_TMP, "runs"))

from chonkvid import risk  # noqa: E402
from chonkvid.sim import EpidemicConfig, choose_vaccinated  # noqa: E402
from chonkvid.world import build_world  # noqa: E402


def _world():
    # Six agents: 0-1 low degree, 2-3 mid, 4-5 high; 5 is in the group chat.
    degs = [1, 1, 3, 3, 6, 6]
    return {"nodes": [{"id": i, "degree": d, "venue": i == 5, "role": "a support agent" if i % 2 else "a QA agent"}
                      for i, d in enumerate(degs)]}


def _run(label, infected, source="live", vaccinated=(), p0=0):
    return [
        {"type": "start", "config": {"label": label}, "world": _world(),
         "vaccinated": list(vaccinated), "patient_zero": p0, "source": source},
        *[{"type": "infection", "agent": a} for a in [p0, *infected]],
        {"type": "end", "summary": {"attack_rate": len(infected) / 5}, "source": source},
    ]


def test_attack_labels_map_to_chonkvid():
    assert risk.attack_of({"label": ""}) == "chonkvid"
    assert risk.attack_of({"label": "targeted"}) == "chonkvid"
    assert risk.attack_of({"label": "morris2"}) == "morris2"


def test_contact_tiers():
    t = risk.tiers(_world()["nodes"])
    assert [t[i] for i in range(6)] == ["low", "low", "mid", "mid", "high", "high"]
    same = risk.tiers([{"id": i, "degree": 4} for i in range(5)])
    assert set(same.values()) == {"mid"}


def test_high_contact_agents_get_higher_risk():
    runs = [("r%d" % i, _run("morris2", infected=[4, 5])) for i in range(5)]
    out = risk.forecast(_world(), runs)["attacks"]["morris2"]
    a = out["agents"]
    assert out["runs"] == 5 and out["source"] == "live"
    assert a[5]["risk"] > a[2]["risk"] > 0
    assert a[4]["risk"] > a[1]["risk"]
    # shrinkage: never exactly 0 or 1 from a handful of runs
    assert 0 < a[1]["risk"] < 1 and a[5]["risk"] < 1


def test_patient_zero_is_not_counted_as_infected():
    runs = [("r", _run("chonkvid", infected=[], p0=0))]
    out = risk.forecast(_world(), runs)["attacks"]["chonkvid"]
    assert out["unvaccinated"]["k"] == 0 and out["unvaccinated"]["n"] == 5


def test_live_runs_override_mock_runs():
    runs = [("m", _run("echoleak", infected=[1, 2, 3, 4, 5], source="mock")),
            ("l", _run("echoleak", infected=[], source="live"))]
    out = risk.forecast(_world(), runs)["attacks"]["echoleak"]
    assert out["source"] == "live" and out["runs"] == 1
    assert out["other_sources"] == {"mock": 1}
    assert out["unvaccinated"]["rate"] == 0


def test_vaccinated_agents_tallied_separately():
    runs = [("r", _run("chonkvid", infected=[1, 2], vaccinated=[4, 5]))]
    out = risk.forecast(_world(), runs)["attacks"]["chonkvid"]
    assert out["vaccinated"] == {"k": 0, "n": 2, "rate": 0.0}
    assert out["unvaccinated"]["n"] == 3


def test_risk_policy_vaccinates_in_forecast_order():
    w = build_world(20, 0)
    cfg = EpidemicConfig(n=20, seed=0, policy="risk", k=3, risk_order=[7, 7, 0, 99, 3, 5, 9])
    # 0 is patient zero, 99 is not in this world, 7 appears twice
    assert choose_vaccinated(w, cfg, patient_zero=0) == [3, 5, 7]


def test_forecast_runs_on_real_recorded_events(tmp_path):
    from chonkvid.detect import Detector
    from chonkvid.llm import MockLLM
    from chonkvid.config import settings
    from chonkvid.sim import run_epidemic

    async def record():
        llm = MockLLM(settings())
        cfg = EpidemicConfig(n=20, seed=1, ticks=6, label="agent-smith")
        return [ev async for ev in run_epidemic(llm, Detector(llm, settings().threshold), cfg)]

    events = asyncio.run(record())
    world = build_world(20, 1).to_json()
    out = risk.forecast(world, [("x", events)])
    assert "agent-smith" in out["attacks"]
    assert len(out["attacks"]["agent-smith"]["agents"]) == 20
