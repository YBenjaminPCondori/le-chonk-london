"""Outbreak forecast with Mistral's Timestral time-series model.

After every resolved tick the dashboard asks Timestral where the S and I curves go
for the rest of the run, as a probabilistic forecast (10th / 50th / 90th percentile).
At the end of the run the forecasts made at FORECAST_CHECK_TICKS are scored against
what actually happened, so the forecast is a measured claim, not decoration.

Optional and fail-safe: if `timestral` is not installed or FORECAST=0, `available()`
is False and the server emits no forecast events. Runs on CPU; no API key needed.

Env:
  FORECAST=0                         disable
  TIMESTRAL_MODEL=mistralai/Timestral-209M   HF id (5M, 28M, 123M, 209M) or a local folder
                                     holding config.json + model.safetensors
  FORECAST_MIN_TICKS=3               context points needed before the first forecast
  FORECAST_CHECK_TICKS=2,5           forecasts scored at the end of the run
"""

from __future__ import annotations

import os
import threading
import time

import numpy as np

from . import config  # noqa: F401  (loads .env, e.g. a local TIMESTRAL_MODEL folder)

QUANTILES = (0.1, 0.5, 0.9)
VARIATES = ("S", "I")

_lock = threading.Lock()
_model = None
_load_error: str | None = None


def model_id() -> str:
    return os.environ.get("TIMESTRAL_MODEL", "mistralai/Timestral-209M")


def model_label() -> str:
    """Display name only: never put a local path on the projector."""
    return os.path.basename(model_id().rstrip("\\/"))


def min_ticks() -> int:
    return max(2, int(os.environ.get("FORECAST_MIN_TICKS", "3")))


def check_ticks() -> list[int]:
    """Ticks whose forecasts get scored at the end of a run (earliest one available)."""
    raw = os.environ.get("FORECAST_CHECK_TICKS", "2,5")
    return sorted({int(x) for x in raw.split(",") if x.strip().isdigit()})


def available() -> bool:
    if os.environ.get("FORECAST", "1").strip().lower() in ("0", "false", "off", "no"):
        return False
    try:
        import timestral  # noqa: F401
    except Exception:
        return False
    return _load_error is None


def load():
    """Load (once) and return the forecaster. Raises on failure; the error is cached."""
    global _model, _load_error
    with _lock:
        if _model is not None:
            return _model
        if _load_error is not None:
            raise RuntimeError(_load_error)
        try:
            from timestral import TimestralForecaster

            _model = TimestralForecaster.from_pretrained(model_id())
        except Exception as e:  # cache so a broken install is tried only once
            _load_error = f"Timestral load failed: {e}"[:300]
            raise RuntimeError(_load_error) from e
        return _model


def predict(history: list[dict], n: int, horizon: int, forecaster=None) -> dict | None:
    """history: [{"tick": t, "counts": {"S":..,"E":..,"I":..,"R":..}}, ...] (resolved ticks).

    Returns a `forecast` event, or None when there is too little context or no horizon.
    """
    if horizon < 1 or len(history) < min_ticks():
        return None
    fc_model = forecaster if forecaster is not None else load()
    ctx = np.array([[h["counts"].get(v, 0) for h in history] for v in VARIATES],
                   dtype=np.float32)[None, :, :]                     # (1, V, T)
    t0 = time.perf_counter()
    out = fc_model.forecast(ctx, prediction_length=horizon)
    ms = round((time.perf_counter() - t0) * 1000)
    last = history[-1]["tick"]
    ev = {"type": "forecast", "tick": last, "model": model_label(), "horizon": horizon,
          "ticks": list(range(last + 1, last + 1 + horizon)), "context": len(history),
          "ms": ms}
    for vi, v in enumerate(VARIATES):
        bands = {}
        for q in QUANTILES:
            arr = np.clip(np.asarray(out.quantile(q))[0, vi, :horizon], 0, n)
            if v == "S":  # immunity is permanent: susceptibles can only fall
                arr = np.minimum.accumulate(np.minimum(arr, history[-1]["counts"]["S"]))
            bands[f"p{int(q * 100)}"] = arr
        # quantile crossing guard: keep p10 <= p50 <= p90
        lo = bands["p10"]
        mid = np.maximum(bands["p50"], lo)
        hi = np.maximum(bands["p90"], mid)
        ev[v] = {k: [round(float(x), 2) for x in a]
                 for k, a in (("p10", lo), ("p50", mid), ("p90", hi))}
    return ev


def score(forecasts: list[dict], history: list[dict], n: int,
          made_at: int | None = None) -> dict | None:
    """Score one forecast against the realised run: the first one made at or after
    `made_at` (default: the earliest).

    Measures the attack size (agents ever infected = n - S) because that is the
    number the experiment reports. Returns a `forecast_check` event or None.
    """
    if not forecasts or not history:
        return None
    pool = [f for f in forecasts if made_at is None or f["tick"] >= made_at]
    if not pool:
        return None
    fc = min(pool, key=lambda f: f["tick"])
    actual = {h["tick"]: h["counts"] for h in history}
    pairs = [(t, i) for i, t in enumerate(fc["ticks"]) if t in actual]
    if not pairs:
        return None
    err, inside = [], 0
    for t, i in pairs:
        ever = n - actual[t]["S"]
        p50 = n - fc["S"]["p50"][i]
        # S band inverts: low S quantile = high attack size
        lo, hi = n - fc["S"]["p90"][i], n - fc["S"]["p10"][i]
        err.append(abs(ever - p50))
        inside += lo - 1e-9 <= ever <= hi + 1e-9
    t_last, i_last = pairs[-1]
    others = max(1, n - 1)
    return {"type": "forecast_check", "made_at": fc["tick"], "context": fc["context"],
            "model": fc["model"], "scored_ticks": len(pairs),
            "mae_agents": round(float(np.mean(err)), 2),
            "coverage_80": round(inside / len(pairs), 2),
            "pred_final_ever": round(n - fc["S"]["p50"][i_last], 1),
            "actual_final_ever": n - actual[t_last]["S"],
            "pred_attack_rate": round((n - fc["S"]["p50"][i_last] - 1) / others, 3),
            "actual_attack_rate": round((n - actual[t_last]["S"] - 1) / others, 3),
            "final_tick": t_last}


def score_all(forecasts: list[dict], history: list[dict], n: int) -> list[dict]:
    """One `forecast_check` per entry in check_ticks(), skipping duplicates."""
    out, seen = [], set()
    for t in check_ticks():
        chk = score(forecasts, history, n, made_at=t)
        if chk and chk["made_at"] not in seen:
            seen.add(chk["made_at"])
            out.append(chk)
    return out
