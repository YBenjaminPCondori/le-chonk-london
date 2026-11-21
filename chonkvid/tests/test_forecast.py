"""Forecast module: shapes, clipping and scoring, with a fake Timestral (no download)."""

import numpy as np

from chonkvid import forecast

LEVELS = (0.01, 0.05) + tuple(round(0.1 + 0.05 * i, 2) for i in range(17)) + (0.95, 0.99)


class _Out:
    def __init__(self, q):  # q: (B, V, H, 21)
        self.quantiles = q

    def quantile(self, level):
        return self.quantiles[..., LEVELS.index(round(level, 2))]


class FakeForecaster:
    """Persists the last value; quantile k adds (k - 10) so the band is symmetric."""

    def forecast(self, ctx, prediction_length):
        b, v, _ = ctx.shape
        last = ctx[:, :, -1:, None]
        offs = np.arange(21, dtype=np.float32) - 10
        return _Out(np.broadcast_to(last, (b, v, prediction_length, 21)) + offs)


class RisingForecaster:
    """Every quantile rises, which S must never do."""

    def forecast(self, ctx, prediction_length):
        b, v, _ = ctx.shape
        ramp = np.arange(1, prediction_length + 1, dtype=np.float32)[None, None, :, None]
        return _Out(np.broadcast_to(ctx[:, :, -1:, None] + 5 * ramp, (b, v, prediction_length, 21)))


def _hist(seq_s, seq_i):
    return [{"tick": t, "counts": {"S": s, "E": 0, "I": i, "R": 0}}
            for t, (s, i) in enumerate(zip(seq_s, seq_i))]


def test_too_little_context_returns_none():
    assert forecast.predict(_hist([39, 37], [1, 3]), 40, 5, FakeForecaster()) is None


def test_no_horizon_returns_none():
    assert forecast.predict(_hist([39, 37, 33], [1, 3, 7]), 40, 0, FakeForecaster()) is None


def test_predict_shapes_and_clipping():
    ev = forecast.predict(_hist([39, 37, 33], [1, 3, 7]), 40, 4, FakeForecaster())
    assert ev["type"] == "forecast" and ev["tick"] == 2 and ev["ticks"] == [3, 4, 5, 6]
    for v in ("S", "I"):
        lo, mid, hi = (np.array(ev[v][k]) for k in ("p10", "p50", "p90"))
        assert len(mid) == 4
        assert (lo <= mid).all() and (mid <= hi).all()
        assert (lo >= 0).all() and (hi <= 40).all()
    assert ev["S"]["p50"] == [33.0] * 4


def test_susceptibles_never_rise():
    ev = forecast.predict(_hist([39, 37, 33], [1, 3, 7]), 40, 4, RisingForecaster())
    for k in ("p10", "p50", "p90"):
        s = ev["S"][k]
        assert max(s) <= 33 and all(a >= b for a, b in zip(s, s[1:]))
    assert ev["I"]["p50"][-1] > 7   # I is free to rise


def test_model_label_hides_local_path(monkeypatch):
    monkeypatch.setenv("TIMESTRAL_MODEL", r"C:\Users\someone\models\Timestral-209M")
    assert forecast.model_label() == "Timestral-209M"
    ev = forecast.predict(_hist([39, 37, 33], [1, 3, 7]), 40, 2, FakeForecaster())
    assert ev["model"] == "Timestral-209M"


def test_score_perfect_persistence():
    hist = _hist([39, 37, 33, 33, 33, 33], [1, 3, 7, 7, 7, 7])
    fc = forecast.predict(hist[:3], 40, 3, FakeForecaster())
    chk = forecast.score([fc], hist, 40)
    assert chk["made_at"] == 2 and chk["scored_ticks"] == 3
    assert chk["mae_agents"] == 0 and chk["coverage_80"] == 1.0
    assert chk["actual_final_ever"] == 7
    assert chk["actual_attack_rate"] == round(6 / 39, 3)


def test_score_detects_miss():
    hist = _hist([39, 37, 33, 20, 10, 5], [1, 3, 7, 15, 20, 18])
    fc = forecast.predict(hist[:3], 40, 3, FakeForecaster())
    chk = forecast.score([fc], hist, 40)
    assert chk["mae_agents"] > 10 and chk["coverage_80"] == 0.0


def test_score_all_checks_tick2_and_tick5(monkeypatch):
    monkeypatch.delenv("FORECAST_CHECK_TICKS", raising=False)
    hist = _hist([39, 37, 33, 30, 25, 20, 18, 17, 17], [1, 3, 7, 9, 12, 14, 12, 9, 6])
    fcs = [forecast.predict(hist[:t + 1], 40, 8 - t, FakeForecaster()) for t in range(2, 8)]
    checks = forecast.score_all(fcs, hist, 40)
    assert [c["made_at"] for c in checks] == [2, 5]
    assert checks[1]["context"] == 6 and checks[1]["scored_ticks"] == 3
    # later forecast has seen more of the outbreak, so it errs less here
    assert checks[1]["mae_agents"] < checks[0]["mae_agents"]


def test_score_all_short_run_dedupes(monkeypatch):
    monkeypatch.delenv("FORECAST_CHECK_TICKS", raising=False)
    hist = _hist([39, 37, 33, 30], [1, 3, 7, 9])
    fcs = [forecast.predict(hist[:3], 40, 1, FakeForecaster())]
    assert [c["made_at"] for c in forecast.score_all(fcs, hist, 40)] == [2]


def test_score_empty():
    assert forecast.score([], [], 40) is None
