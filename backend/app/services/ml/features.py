"""Feature and target construction for NAV forecasting.

Leakage rules enforced here:

* Every feature at date t uses only observations dated <= t (trailing
  windows, no centred rolling statistics, no full-sample normalisation).
* The target is the *future* log return over ``horizon`` trading days:
  ``log(NAV[t+h] / NAV[t])``. Rows whose target would need data beyond the
  last observation are excluded from training and used only for the live
  forecast.
* Scaling is fitted inside the model pipeline on training rows only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_VERSION = "v1"

FEATURE_LABELS = {
    "ret_1d": "1-day return",
    "ret_5d": "5-day return",
    "ret_21d": "1-month return",
    "ret_63d": "3-month return",
    "vol_21d": "1-month volatility",
    "vol_63d": "3-month volatility",
    "vol_ratio": "short/long volatility ratio",
    "drawdown_252d": "distance below 1-year high",
    "bench_ret_5d": "benchmark 5-day return",
    "bench_ret_21d": "benchmark 1-month return",
    "rel_ret_21d": "1-month return vs benchmark",
}
WARMUP_DAYS = 252


def build_features(nav: pd.Series, benchmark: pd.Series | None = None) -> pd.DataFrame:
    if len(nav) < WARMUP_DAYS + 30:
        from app.core.errors import InsufficientDataError

        raise InsufficientDataError(
            f"Forecasting needs at least {WARMUP_DAYS + 30} NAV observations; only {len(nav)} available."
        )
    log_nav = np.log(nav.astype(float))
    r1 = log_nav.diff()
    frame = pd.DataFrame(index=nav.index)
    frame["ret_1d"] = r1
    frame["ret_5d"] = log_nav.diff(5)
    frame["ret_21d"] = log_nav.diff(21)
    frame["ret_63d"] = log_nav.diff(63)
    frame["vol_21d"] = r1.rolling(21).std() * np.sqrt(252)
    frame["vol_63d"] = r1.rolling(63).std() * np.sqrt(252)
    frame["vol_ratio"] = frame["vol_21d"] / frame["vol_63d"]
    frame["drawdown_252d"] = nav / nav.rolling(252, min_periods=252).max() - 1.0
    if benchmark is not None:
        bench = np.log(benchmark.astype(float)).reindex(nav.index).ffill()
        frame["bench_ret_5d"] = bench.diff(5)
        frame["bench_ret_21d"] = bench.diff(21)
        frame["rel_ret_21d"] = frame["ret_21d"] - frame["bench_ret_21d"]
    return frame


def build_target(nav: pd.Series, horizon: int) -> pd.Series:
    log_nav = np.log(nav.astype(float))
    return (log_nav.shift(-horizon) - log_nav).rename("target")


def make_dataset(
    nav: pd.Series, benchmark: pd.Series | None, horizon: int
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Returns (X, y) for rows with a known target, plus the latest feature row."""
    features = build_features(nav, benchmark)
    target = build_target(nav, horizon)
    complete = features.dropna()
    labelled = complete.loc[target.loc[complete.index].notna()]
    latest = complete.iloc[[-1]]
    return labelled, target.loc[labelled.index], latest


def chronological_split(n_rows: int, horizon: int, train_frac: float = 0.7, val_frac: float = 0.15) -> dict:
    """Index ranges for train / validation / test with a purge gap of ``horizon`` rows.

    Targets overlap in time (each spans ``horizon`` days), so without the gap
    the last training targets would contain prices from the validation
    period.
    """
    train_end = int(n_rows * train_frac)
    val_start = train_end + horizon
    val_end = int(n_rows * (train_frac + val_frac))
    test_start = val_end + horizon
    if train_end < 200 or val_end - val_start < 40 or n_rows - test_start < 40:
        from app.core.errors import InsufficientDataError

        raise InsufficientDataError(
            "Not enough history for a train/validation/test split at this horizon.",
            {"rows": n_rows, "horizon": horizon},
        )
    return {"train": (0, train_end), "val": (val_start, val_end), "test": (test_start, n_rows)}
